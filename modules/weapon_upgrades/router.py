# router.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.keyboards import char_equipment_kb

from .keyboards import weapon_upgrade_pick_weapon_kb, weapon_upgrade_slot_kb
from .service import WeaponUpgradeService
from .states import WeaponUpgradeStates


router = Router()



async def _deny_if_in_active_raid_tg(tg_id: int, session: AsyncSession, character_id: int) -> bool:
    row = (
        await session.execute(
            text(
                """
                SELECT 1
                FROM raids r
                JOIN characters c ON c.id = r.character_id
                JOIN users u ON u.id = c.user_id
                WHERE u.tg_id = :tg
                  AND c.id = :cid
                  AND r.status = 'active'
                LIMIT 1
                """
            ),
            {"tg": int(tg_id), "cid": int(character_id)},
        )
    ).first()
    return bool(row)


async def _deny_if_in_active_raid_call(call: CallbackQuery, session: AsyncSession, character_id: int) -> bool:
    if await _deny_if_in_active_raid_tg(int(call.from_user.id), session, int(character_id)):
        await call.answer("Персонаж в рейде. Действия недоступны.", show_alert=True)
        return True
    return False

def _bonus_int(total_bonus: dict | None, key: str) -> int:
    if not total_bonus:
        return 0
    try:
        return int(total_bonus.get(key, 0) or 0)
    except Exception:
        return 0


async def _compose_slot_lines(svc: WeaponUpgradeService, character_id: int, slot: int, weapon_id: int) -> list[str]:
    weapon = await svc.get_weapon(weapon_id)
    if not weapon:
        return ["Оружие не найдено."]

    ammo = await svc.get_best_ammo_for_caliber(weapon.caliber_id)
    unique = await svc.get_unique_info(weapon_id)
    inv_mods = await svc.list_inventory_mods(character_id)

    base_id = unique.base_weapon_id if unique else weapon.id
    installed_types: set[str] = set()
    installed_lines: list[str] = []

    if unique and unique.mods:
        for m in unique.mods:
            mt = str(m.get("mod_type", ""))
            if mt:
                installed_types.add(mt)
            installed_lines.append(f"- {m.get('name','')} (ID {m.get('item_id')})")
    else:
        installed_lines.append("- нет")

    total_bonus = unique.total_bonus if unique else {}
    acc_b = _bonus_int(total_bonus, "accuracy_bonus")
    rel_b = _bonus_int(total_bonus, "reliability_bonus")
    dmg_b = _bonus_int(total_bonus, "damage_bonus")
    pen_b = _bonus_int(total_bonus, "armor_pen_bonus")
    loot_b = _bonus_int(total_bonus, "loot_analysis_bonus")

    ammo_line = "- нет данных"
    if ammo:
        final_dmg = ammo.damage + dmg_b
        final_pen = ammo.armor_penetration + pen_b
        ammo_line = (
            f"{ammo.name} | DMG {ammo.damage}{dmg_b:+} = {final_dmg} | "
            f"PEN {ammo.armor_penetration}{pen_b:+} = {final_pen}"
        )

    bonus_line = "- нет"
    if any(v != 0 for v in (acc_b, rel_b, dmg_b, pen_b, loot_b)):
        bonus_line = f"ACC {acc_b:+} REL {rel_b:+} DMG {dmg_b:+} PEN {pen_b:+} LOOT {loot_b:+}"

    mod_lines: list[str] = []
    shown_any = False
    if inv_mods:
        for m in inv_mods:
            if not m.is_compatible(weapon.category, base_id, installed_types=installed_types):
                continue
            if m.mod_type in installed_types:
                continue

            shown_any = True
            loot_tail = f" LOOT {m.loot_analysis_bonus:+}" if int(m.loot_analysis_bonus or 0) != 0 else ""
            mod_lines.append(
                f"ID {m.item_id} ×{m.qty} – {m.name} | {m.mod_type}/{m.tier} | "
                f"ACC {m.accuracy_bonus:+} REL {m.reliability_bonus:+} DMG {m.damage_bonus:+} PEN {m.armor_pen_bonus:+}{loot_tail}"
            )

    if not shown_any:
        mod_lines.append("- нет подходящих модификаций в инвентаре")

    weapon_lines = [
        f"Улучшение оружия – слот {slot}",
        "",
        f"Оружие: {weapon.name} (ID {weapon.id})",
        f"Категория: {weapon.category}",
        f"Характеристики оружия: ACC {weapon.accuracy} | REL {weapon.reliability}",
        f"Бонусы модов: {bonus_line}",
        "",
        "Боевые патроны:",
        ammo_line,
        "",
        "Установленные моды:",
        *installed_lines,
        "",
        "Моды в рюкзаке:",
        *mod_lines,
        "",
        "Чтобы установить мод, отправь в чат ID модификации из списка.",
    ]

    return weapon_lines


async def _render_pick(call: CallbackQuery, db_session: AsyncSession, character_id: int):
    svc = WeaponUpgradeService(db_session)
    weapons = await svc.get_equipped_weapons(character_id)

    if not weapons:
        text = "У персонажа нет экипированного оружия."
        await safe_edit(call, text, reply_markup=char_equipment_kb(character_id))
        return

    lines = ["Улучшение оружия", "", "Выбери слот:"]
    for slot in sorted(weapons.keys()):
        w = weapons[slot]
        lines.append(f"{slot}. {w.name} (ID {w.id})")

    await safe_edit(
        call,
        "\n".join(lines),
        reply_markup=weapon_upgrade_pick_weapon_kb(character_id, weapons),
    )


async def _render_slot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, character_id: int, slot: int):
    svc = WeaponUpgradeService(db_session)

    weapon_id = await svc.get_weapon_id_in_slot(character_id, slot)
    if not weapon_id:
        await safe_edit(
            call,
            "В этом слоте нет оружия.",
            reply_markup=weapon_upgrade_pick_weapon_kb(character_id, await svc.get_equipped_weapons(character_id)),
        )
        return

    lines = await _compose_slot_lines(svc, character_id, slot, weapon_id)

    await state.set_state(WeaponUpgradeStates.waiting_mod_item_id)
    await state.update_data(
        character_id=character_id,
        slot=slot,
        weapon_id=weapon_id,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    await safe_edit(call, "\n".join(lines), reply_markup=weapon_upgrade_slot_kb(character_id, slot))


@router.callback_query(F.data.startswith("wup:open:"))
async def wup_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    character_id = int(call.data.split(":")[2])
    if await _deny_if_in_active_raid_call(call, db_session, character_id):
        return
    await _render_pick(call, db_session, character_id)


@router.callback_query(F.data.startswith("wup:slot:"))
async def wup_slot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    _, _, character_id_s, slot_s = call.data.split(":", 3)
    character_id = int(character_id_s)
    if await _deny_if_in_active_raid_call(call, db_session, character_id):
        return
    await _render_slot(call, db_session, state, character_id, int(slot_s))


@router.callback_query(F.data.startswith("wup:cancel:"))
async def wup_cancel(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    _, _, character_id_s, slot_s = call.data.split(":", 3)
    character_id = int(character_id_s)
    if await _deny_if_in_active_raid_call(call, db_session, character_id):
        return
    await _render_slot(call, db_session, state, character_id, int(slot_s))


@router.message(WeaponUpgradeStates.waiting_mod_item_id)
async def wup_apply_from_chat(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    character_id = int(data["character_id"])
    if await _deny_if_in_active_raid_tg(int(message.from_user.id), db_session, character_id):
        await state.clear()
        await message.answer("Персонаж в рейде. Улучшение оружия недоступно.")
        return

    slot = int(data["slot"])
    expected_weapon_id = int(data["weapon_id"])
    chat_id = int(data["chat_id"])
    message_id = int(data["message_id"])

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой ID модификации.")
        return

    mod_item_id = int(raw)
    svc = WeaponUpgradeService(db_session)

    try:
        new_weapon = await svc.apply_mod(
            character_id=character_id,
            slot=slot,
            expected_weapon_id=expected_weapon_id,
            mod_item_id=mod_item_id,
        )
    except ValueError as e:
        await message.answer(str(e))
        return

    await message.answer(
        f"Мод установлен. Снять апгрейд нельзя. Новое оружие: {new_weapon.name} (ID {new_weapon.id})."
    )

    await state.update_data(weapon_id=new_weapon.id)
    lines = await _compose_slot_lines(svc, character_id, slot, new_weapon.id)

    try:
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\n".join(lines),
            reply_markup=weapon_upgrade_slot_kb(character_id, slot),
            parse_mode="HTML",
        )
    except Exception:
        return
