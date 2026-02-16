from __future__ import annotations

import logging
import random

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.mechanics.clash import (
    CombatantState,
    InjuryState,
    WeaponSnapshot,
    compute_defense_pct,
    geometric_mean_pct,
    simulate_clash_round,
)
from modules.common.tg import safe_edit, safe_delete
from modules.start.keyboards import (
    my_chars_kb,
    chars_pick_kb,
    char_detail_kb,
    char_physical_kb,
    char_equipment_kb,
    char_stash_kb,
    clash_pick_enemy_kb,
    clash_result_kb,
)
from modules.start.service import StartService
from modules.stash.service import StashService

router = Router()
log = logging.getLogger(__name__)


def _esc(s: object) -> str:
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _to_float(x: object | None) -> float:
    if x is None:
        return 0.0
    try:
        return float(x)
    except Exception:
        return 0.0


def _clamp_0_100(x: object | None) -> float:
    v = _to_float(x)
    if v < 0:
        return 0.0
    if v > 100:
        return 100.0
    return v


async def _show(call: CallbackQuery, text_out: str, reply_markup):
    msg = call.message
    if msg and msg.text is not None:
        await safe_edit(call, text_out, reply_markup=reply_markup)
        return
    await safe_delete(msg)
    if msg:
        await msg.answer(text_out, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)


def _clash_behavior_kb(character_id: int, enemy_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Агрессивный",
                    callback_data=f"clash:run:{character_id}:{enemy_key}:aggressive",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Скрытный",
                    callback_data=f"clash:run:{character_id}:{enemy_key}:stealth",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data=f"clash:test:{character_id}")],
        ]
    )


async def _ammo_stats(session: AsyncSession, caliber_id: object | None) -> tuple[int, int]:
    if not caliber_id:
        return 0, 0
    ammo = (
        await session.execute(
            text(
                """
                SELECT damage, armor_penetration
                FROM ammo_types
                WHERE caliber_id = :cid
                ORDER BY id
                LIMIT 1
                """
            ),
            {"cid": caliber_id},
        )
    ).mappings().first()
    if not ammo:
        return 0, 0
    return int(ammo.get("damage") or 0), int(ammo.get("armor_penetration") or 0)


async def _random_weapon_for_categories(session: AsyncSession, categories: list[str]) -> WeaponSnapshot | None:
    if not categories:
        return None
    ph = ", ".join([f":c{i}" for i in range(len(categories))])
    params = {f"c{i}": categories[i] for i in range(len(categories))}
    row = (
        await session.execute(
            text(
                f"""
                SELECT
                  w.name,
                  w.category,
                  w.accuracy,
                  w.reliability,
                  w.caliber_id,
                  COALESCE(c.code, '') AS caliber
                FROM weapons w
                LEFT JOIN calibers c ON c.id = w.caliber_id
                WHERE w.category IN ({ph})
                ORDER BY RANDOM()
                LIMIT 1
                """
            ),
            params,
        )
    ).mappings().first()
    if not row:
        return None

    dmg, ap = await _ammo_stats(session, row.get("caliber_id"))
    return WeaponSnapshot(
        name=str(row.get("name") or "Оружие"),
        category=str(row.get("category") or "rifle"),
        caliber=str(row.get("caliber") or ""),
        accuracy=int(row.get("accuracy") or 50),
        reliability=int(row.get("reliability") or 0),
        dmg=dmg,
        ap=ap,
    )


async def _npc_by_key(session: AsyncSession, key: str) -> CombatantState:
    if key == "mannequin":
        w = await _random_weapon_for_categories(session, ["pistol"])
        if not w:
            w = WeaponSnapshot(
                name="Training Pistol",
                category="pistol",
                caliber="9×19",
                accuracy=50,
                reliability=80,
                dmg=16,
                ap=2,
            )
        return CombatantState(
            name="Манекен",
            hp_max=100,
            hp_current=100,
            accuracy=40,
            reaction=0.0,
            initiative=0.0,
            stealth=0.0,
            defense_base_pct=20.0,
            rel_armor_pct=100.0,
            weapons=[w],
            behavior="aggressive",
            injuries=InjuryState(),
        )

    if key == "raider":
        w = await _random_weapon_for_categories(session, ["pistol", "smg", "shotgun"])
        if not w:
            w = WeaponSnapshot(
                name="PP-19 Bizon",
                category="smg",
                caliber="9×19",
                accuracy=64,
                reliability=64,
                dmg=16,
                ap=2,
            )
        return CombatantState(
            name="Рейдер",
            hp_max=110,
            hp_current=110,
            accuracy=45,
            reaction=8.0,
            initiative=10.0,
            stealth=6.0,
            defense_base_pct=25.0,
            rel_armor_pct=90.0,
            weapons=[w],
            behavior="aggressive",
            injuries=InjuryState(),
        )

    if key == "guard":
        w = await _random_weapon_for_categories(session, ["smg", "shotgun", "rifle", "lmg"])
        if not w:
            w = WeaponSnapshot(
                name="MP5K",
                category="smg",
                caliber="9×19",
                accuracy=66,
                reliability=62,
                dmg=16,
                ap=2,
            )
        return CombatantState(
            name="Охранник",
            hp_max=120,
            hp_current=120,
            accuracy=55,
            reaction=10.0,
            initiative=12.0,
            stealth=8.0,
            defense_base_pct=35.0,
            rel_armor_pct=95.0,
            weapons=[w],
            behavior="aggressive",
            injuries=InjuryState(),
        )

    raise ValueError("unknown npc")


async def _character_owned(session: AsyncSession, tg_id: int, character_id: int) -> bool:
    svc = StartService(session)
    user = await svc.ensure_user(tg_id)
    row = (
        await session.execute(
            text("SELECT 1 FROM characters WHERE id = :cid AND user_id = :uid"),
            {"cid": character_id, "uid": user.id},
        )
    ).first()
    return bool(row)


async def _load_character_state(session: AsyncSession, tg_id: int, character_id: int) -> CombatantState:
    svc = StartService(session)
    user = await svc.ensure_user(tg_id)

    row = (
        await session.execute(
            text(
                """
                SELECT
                  c.id,
                  c.name,
                  c.hp AS max_hp,
                  COALESCE(h.current_hp, c.hp) AS current_hp,
                  COALESCE(h.head_injury, 0) AS head_injury,
                  COALESCE(h.torso_injury, 0) AS torso_injury,
                  COALESCE(h.arm_injury, 0) AS arm_injury,
                  COALESCE(h.leg_injury, 0) AS leg_injury,

                  c.accuracy AS base_accuracy,
                  c.reaction AS base_reaction,
                  c.initiative AS base_initiative,
                  c.stealth AS base_stealth,
                  c.carry_capacity AS base_carry,

                  COALESCE(sh.accuracy_bonus, 0) + COALESCE(sb.accuracy_bonus, 0) + COALESCE(sg.accuracy_bonus, 0) + COALESCE(st.accuracy_bonus, 0) AS acc_bonus,
                  COALESCE(sh.reaction_bonus, 0) + COALESCE(sb.reaction_bonus, 0) + COALESCE(sg.reaction_bonus, 0) + COALESCE(st.reaction_bonus, 0) AS reaction_bonus,
                  COALESCE(sh.initiative_bonus, 0) + COALESCE(sb.initiative_bonus, 0) + COALESCE(sg.initiative_bonus, 0) + COALESCE(st.initiative_bonus, 0) AS initiative_bonus,
                  COALESCE(sh.stealth_bonus, 0) + COALESCE(sb.stealth_bonus, 0) + COALESCE(sg.stealth_bonus, 0) + COALESCE(st.stealth_bonus, 0) AS stealth_bonus,
                  COALESCE(sh.carry_capacity_bonus, 0) + COALESCE(sb.carry_capacity_bonus, 0) + COALESCE(sg.carry_capacity_bonus, 0) + COALESCE(st.carry_capacity_bonus, 0) AS carry_bonus,

                  COALESCE(sh.armor, 0) AS head_armor,
                  COALESCE(sb.armor, 0) AS body_armor,
                  COALESCE(sh.reliability, 0) AS head_rel,
                  COALESCE(sb.reliability, 0) AS body_rel,

                  COALESCE(ih.weight, 0) AS head_weight,
                  COALESCE(ib.weight, 0) AS body_weight,
                  COALESCE(ig.weight, 0) AS gloves_weight,
                  COALESCE(it.weight, 0) AS boots_weight,

                  w1.id AS w1_id,
                  w1.name AS w1_name,
                  w1.category AS w1_cat,
                  w1.accuracy AS w1_acc,
                  w1.reliability AS w1_rel,
                  w1.caliber_id AS w1_caliber_id,
                  COALESCE(w1.weight_kg, 0) AS w1_weight,
                  c1.code AS w1_caliber,

                  w2.id AS w2_id,
                  w2.name AS w2_name,
                  w2.category AS w2_cat,
                  w2.accuracy AS w2_acc,
                  w2.reliability AS w2_rel,
                  w2.caliber_id AS w2_caliber_id,
                  COALESCE(w2.weight_kg, 0) AS w2_weight,
                  c2.code AS w2_caliber,

                  w3.id AS w3_id,
                  w3.name AS w3_name,
                  w3.category AS w3_cat,
                  w3.accuracy AS w3_acc,
                  w3.reliability AS w3_rel,
                  w3.caliber_id AS w3_caliber_id,
                  COALESCE(w3.weight_kg, 0) AS w3_weight,
                  c3.code AS w3_caliber

                FROM characters c
                LEFT JOIN character_health h ON h.character_id = c.id
                LEFT JOIN equipment e ON e.character_id = c.id

                LEFT JOIN items ih ON ih.id = e.head_item_id
                LEFT JOIN item_equipment_stats sh ON sh.item_id = ih.id
                LEFT JOIN items ib ON ib.id = e.body_item_id
                LEFT JOIN item_equipment_stats sb ON sb.item_id = ib.id
                LEFT JOIN items ig ON ig.id = e.gloves_item_id
                LEFT JOIN item_equipment_stats sg ON sg.item_id = ig.id
                LEFT JOIN items it ON it.id = e.boots_item_id
                LEFT JOIN item_equipment_stats st ON st.item_id = it.id

                LEFT JOIN weapons w1 ON w1.id = e.weapon_1_id
                LEFT JOIN calibers c1 ON c1.id = w1.caliber_id
                LEFT JOIN weapons w2 ON w2.id = e.weapon_2_id
                LEFT JOIN calibers c2 ON c2.id = w2.caliber_id
                LEFT JOIN weapons w3 ON w3.id = e.weapon_3_id
                LEFT JOIN calibers c3 ON c3.id = w3.caliber_id

                WHERE c.id = :cid AND c.user_id = :uid
                """
            ),
            {"cid": character_id, "uid": user.id},
        )
    ).mappings().first()

    if not row:
        raise ValueError("character not found")

    name = str(row.get("name") or "Без имени")

    base_accuracy = int(row.get("base_accuracy") or 0)
    base_reaction = _to_float(row.get("base_reaction"))
    base_initiative = _to_float(row.get("base_initiative"))
    base_stealth = _to_float(row.get("base_stealth"))
    base_carry = _to_float(row.get("base_carry"))

    accuracy = base_accuracy + int(row.get("acc_bonus") or 0)
    reaction = base_reaction + _to_float(row.get("reaction_bonus"))
    initiative = base_initiative + _to_float(row.get("initiative_bonus"))
    stealth = base_stealth + _to_float(row.get("stealth_bonus"))
    carry_capacity = max(0.0, base_carry + _to_float(row.get("carry_bonus")))

    total_weight = (
        _to_float(row.get("head_weight"))
        + _to_float(row.get("body_weight"))
        + _to_float(row.get("gloves_weight"))
        + _to_float(row.get("boots_weight"))
        + _to_float(row.get("w1_weight"))
        + _to_float(row.get("w2_weight"))
        + _to_float(row.get("w3_weight"))
    )

    penalty = 0.0
    if carry_capacity > 0 and total_weight > carry_capacity:
        excess_pct = (total_weight / carry_capacity - 1.0) * 100.0
        steps = int(excess_pct // 10.0)
        penalty = 10.0 + max(0, steps - 1) * 5.0

    if penalty > 0:
        k = 1.0 - penalty / 100.0
        reaction *= k
        initiative *= k
        stealth *= k

    head_armor = _clamp_0_100(row.get("head_armor"))
    body_armor = _clamp_0_100(row.get("body_armor"))
    defense_base_pct = compute_defense_pct(head_armor, body_armor)
    rel_armor_pct = geometric_mean_pct([_clamp_0_100(row.get("head_rel")), _clamp_0_100(row.get("body_rel"))])

    weapons: list[WeaponSnapshot] = []
    for p in ("w1", "w2", "w3"):
        if row.get(f"{p}_id") is None:
            continue
        dmg, ap = await _ammo_stats(session, row.get(f"{p}_caliber_id"))
        weapons.append(
            WeaponSnapshot(
                name=str(row.get(f"{p}_name") or "Оружие"),
                category=str(row.get(f"{p}_cat") or "rifle"),
                caliber=str(row.get(f"{p}_caliber") or ""),
                accuracy=int(row.get(f"{p}_acc") or 50),
                reliability=int(row.get(f"{p}_rel") or 0),
                dmg=dmg,
                ap=ap,
            )
        )

    if not weapons:
        raise ValueError("no weapon equipped")

    injuries = InjuryState(
        head=int(row.get("head_injury") or 0),
        torso=int(row.get("torso_injury") or 0),
        arm=int(row.get("arm_injury") or 0),
        leg=int(row.get("leg_injury") or 0),
    )

    return CombatantState(
        name=name,
        hp_max=int(row.get("max_hp") or 0),
        hp_current=int(row.get("current_hp") or 0),
        accuracy=int(accuracy),
        reaction=float(reaction),
        initiative=float(initiative),
        stealth=float(stealth),
        defense_base_pct=float(defense_base_pct),
        rel_armor_pct=float(rel_armor_pct),
        weapons=weapons,
        behavior="aggressive",
        injuries=injuries,
    )


def _fmt_inj(inj: InjuryState) -> str:
    parts: list[str] = []
    if inj.head:
        parts.append(f"Голова {inj.head}")
    if inj.torso:
        parts.append(f"Туловище {inj.torso}")
    if inj.arm:
        parts.append(f"Рука {inj.arm}")
    if inj.leg:
        parts.append(f"Нога {inj.leg}")
    return "–" if not parts else ", ".join(parts)


def _format_clash_text(rnd) -> str:
    a = rnd.a_start
    b = rnd.b_start
    a_end = rnd.a_end
    b_end = rnd.b_end

    def _weapons_line(c: CombatantState) -> str:
        ws = c.weapons or []
        if not ws:
            return "–"
        return ", ".join([f"{w.name} ({w.category})" for w in ws])

    lines: list[str] = []
    lines.append("<b>Тест боя</b>")
    lines.append(f"{_esc(a.name)} – {_esc(_weapons_line(a))}")
    lines.append(f"{_esc(b.name)} – {_esc(_weapons_line(b))}")
    lines.append("")
    lines.append(
        "<pre>"
        f"Поведение: {a.behavior} vs {b.behavior}\n"
        f"Раунды: {rnd.planned_rounds}\n"
        f"Засада: {rnd.ambush.winner if rnd.ambush.winner else 'нет'}\n"
        f"AmbushScore – {_esc(a.name)}: d100 {rnd.ambush.a_roll} + {float(a.stealth):.1f} = {float(rnd.ambush.a_total):.1f}\n"
        f"AmbushScore – {_esc(b.name)}: d100 {rnd.ambush.b_roll} + {float(b.stealth):.1f} = {float(rnd.ambush.b_total):.1f}"
        "</pre>"
    )

    log_lines: list[str] = []
    for ev in rnd.events:
        log_lines.extend(ev.log_lines)
        log_lines.append("")
    log_txt = "\n".join(log_lines).strip()
    log_txt = _esc(log_txt)
    if len(log_txt) > 3200:
        log_txt = log_txt[:3200] + "\n…"

    lines.append("<b>Лог</b>")
    lines.append(f"<pre>{log_txt}</pre>")
    lines.append("<b>Итог</b>")
    lines.append(
        "<pre>"
        f"{_esc(a_end.name)} HP: {a_end.hp_current}/{a_end.hp_max}; травмы: {_esc(_fmt_inj(a_end.injuries))}\n"
        f"{_esc(b_end.name)} HP: {b_end.hp_current}/{b_end.hp_max}; травмы: {_esc(_fmt_inj(b_end.injuries))}"
        "</pre>"
    )
    if rnd.winner:
        lines.append(f"<b>Победитель</b>: {_esc(rnd.winner)}")
    return "\n".join(lines)


@router.callback_query(F.data == "menu:chars")
async def open_chars_menu(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    text_out = await svc.characters_summary_text(call.from_user.id)
    chars = await svc.list_characters(call.from_user.id)
    await _show(call, text_out, reply_markup=my_chars_kb(bool(chars)))
    await call.answer()


@router.callback_query(F.data == "chars:pick")
async def chars_pick(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await call.answer("Персонажей нет.", show_alert=True)
        return
    text_out = "<b>Выбор персонажа</b>\nНажми на персонажа."
    await _show(call, text_out, reply_markup=chars_pick_kb(chars))
    await call.answer()


@router.callback_query(F.data.startswith("chars:open:"))
async def open_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 3 or not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    character_id = int(data[2])

    svc = StartService(db_session)
    try:
        text_out = await svc.character_details_text(tg_id=call.from_user.id, character_id=character_id)
    except Exception:
        await call.answer("Персонаж не найден.", show_alert=True)
        return

    await _show(call, text_out, reply_markup=char_detail_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("char:phys:"))
async def open_physical(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 3 or not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    character_id = int(data[2])

    svc = StartService(db_session)
    try:
        st = await svc.character_physical_state(tg_id=call.from_user.id, character_id=character_id)
    except Exception:
        await call.answer("Персонаж не найден.", show_alert=True)
        return

    msg = call.message

    if st.image_path:
        if msg and msg.photo:
            try:
                await msg.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(st.image_path),
                        caption=st.text,
                        parse_mode="HTML",
                    ),
                    reply_markup=char_physical_kb(character_id),
                )
            except Exception:
                await msg.answer_photo(
                    photo=FSInputFile(st.image_path),
                    caption=st.text,
                    reply_markup=char_physical_kb(character_id),
                    parse_mode="HTML",
                )
        else:
            await call.message.answer_photo(
                photo=FSInputFile(st.image_path),
                caption=st.text,
                reply_markup=char_physical_kb(character_id),
                parse_mode="HTML",
            )
    else:
        await _show(call, st.text, reply_markup=char_physical_kb(character_id))

    await call.answer()


@router.callback_query(F.data.startswith("char:eq:"))
async def open_equipment(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 3 or not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    character_id = int(data[2])

    svc = StartService(db_session)
    try:
        text_out = await svc.character_equipment_text(tg_id=call.from_user.id, character_id=character_id)
    except Exception:
        await call.answer("Персонаж не найден.", show_alert=True)
        return

    await _show(call, text_out, reply_markup=char_equipment_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("char:stash:"))
async def open_stash(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 3 or not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    character_id = int(data[2])

    svc = StashService(db_session)
    try:
        text_out = await svc.character_stash_text(tg_id=call.from_user.id, character_id=character_id)
    except Exception:
        await call.answer("Персонаж не найден.", show_alert=True)
        return

    await _show(call, text_out, reply_markup=char_stash_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("clash:test:"))
async def clash_test_pick(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 3 or not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(data[2])
    if not await _character_owned(db_session, call.from_user.id, character_id):
        await call.answer("Персонаж не найден.", show_alert=True)
        return

    text_out = "<b>Тест боя</b>\nВыбери противника."
    await _show(call, text_out, reply_markup=clash_pick_enemy_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("clash:run:"))
async def clash_test_run(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")

    if len(data) not in (4, 5):
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    if not data[2].isdigit():
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(data[2])
    enemy_key = data[3]

    if len(data) == 4:
        text_out = "<b>Тест боя</b>\nВыбери поведение своего персонажа."
        await _show(call, text_out, reply_markup=_clash_behavior_kb(character_id, enemy_key))
        await call.answer()
        return

    behavior = data[4]
    if behavior not in ("aggressive", "stealth"):
        await call.answer("Некорректное поведение.", show_alert=True)
        return

    try:
        a = await _load_character_state(db_session, call.from_user.id, character_id)
    except Exception:
        await call.answer("Персонаж не найден или нет оружия.", show_alert=True)
        return

    a.behavior = behavior

    try:
        b = await _npc_by_key(db_session, enemy_key)
    except Exception:
        await call.answer("Противник не найден.", show_alert=True)
        return

    rnd = simulate_clash_round(a, b, rng=random.Random())
    text_out = _format_clash_text(rnd)
    await _show(call, text_out, reply_markup=clash_result_kb(character_id, enemy_key))
    await call.answer()
