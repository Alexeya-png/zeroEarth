from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.equip.keyboards import ArmorChoice, equip_main_kb, equip_armor_list_kb, equip_weapon_pick_kb
from modules.equip.service import EquipService, EquipError, SLOT_TITLE


router = Router()
log = logging.getLogger(__name__)


PAGE_SIZE = 8


def _parse_int(x: str) -> int | None:
    try:
        return int(x)
    except Exception:
        return None


@router.callback_query(F.data.startswith("equip:open:"))
async def equip_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    if character_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть снаряжение.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть снаряжение.", show_alert=True)
        return

    await safe_edit(call, svc.equip_text(view), reply_markup=equip_main_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("equip:slot:"))
async def equip_open_armor_slot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    slot_key = data[3]
    page = _parse_int(data[4])
    if character_id is None or page is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        view = await svc.equipment_view(call.from_user.id, character_id)
        rows, total, page = await svc.list_armor_inventory(call.from_user.id, character_id, slot_key, page, PAGE_SIZE)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть слот.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть слот.", show_alert=True)
        return

    title = SLOT_TITLE.get(slot_key, "Слот")
    items = [
        ArmorChoice(
            id=int(r["id"]),
            name=str(r.get("name") or "Предмет"),
            qty=int(r.get("qty") or 1),
            tier=str(r.get("tier") or "").strip(),
            armor=int(r.get("armor") or 0),
            reliability=int(r.get("reliability") or 0),
        )
        for r in rows
    ]

    has_prev = page > 0
    has_next = (page + 1) * PAGE_SIZE < total

    can_unequip = False
    if slot_key == "head":
        can_unequip = bool(view.head_has_item)
    elif slot_key == "body":
        can_unequip = bool(view.body_has_item)
    elif slot_key == "gloves":
        can_unequip = bool(view.gloves_has_item)
    elif slot_key == "boots":
        can_unequip = bool(view.boots_has_item)

    text_out = "\n".join(
        [
            f"<b>{title}</b>",
            "Выбери предмет со склада.",
        ]
    )

    await safe_edit(
        call,
        text_out,
        reply_markup=equip_armor_list_kb(
            character_id=character_id,
            slot_key=slot_key,
            items=items,
            page=page,
            has_prev=has_prev,
            has_next=has_next,
            can_unequip=can_unequip,
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("equip:wear:"))
async def equip_wear_armor(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    slot_key = data[3]
    item_id = _parse_int(data[4])
    if character_id is None or item_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.equip_armor_from_inventory(call.from_user.id, character_id, slot_key, item_id)
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось надеть предмет.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось надеть предмет.", show_alert=True)
        return

    await safe_edit(call, svc.equip_text(view), reply_markup=equip_main_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("equip:unequip:"))
async def equip_unequip_armor(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    slot_key = data[3]
    if character_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.unequip_armor(call.from_user.id, character_id, slot_key)
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось снять предмет.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось снять предмет.", show_alert=True)
        return

    await safe_edit(call, svc.equip_text(view), reply_markup=equip_main_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("equip:wselect:"))
async def equip_weapon_select(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    to_slot = _parse_int(data[3])
    if character_id is None or to_slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть оружие.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть оружие.", show_alert=True)
        return

    from_slots = {k: v for k, v in view.weapons.items() if v}
    text_out = f"<b>Оружие {to_slot}</b>\nВыбери, откуда взять оружие."
    await safe_edit(call, text_out, reply_markup=equip_weapon_pick_kb(character_id, to_slot, from_slots))
    await call.answer()


@router.callback_query(F.data.startswith("equip:wmove:"))
async def equip_weapon_move(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[2])
    from_slot = _parse_int(data[3])
    to_slot = _parse_int(data[4])
    if character_id is None or from_slot is None or to_slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.move_or_swap_weapon(call.from_user.id, character_id, from_slot, to_slot)
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось изменить оружие.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось изменить оружие.", show_alert=True)
        return

    await safe_edit(call, svc.equip_text(view), reply_markup=equip_main_kb(character_id))
    await call.answer()
