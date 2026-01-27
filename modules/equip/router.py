# modules/equip/router.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.equip.keyboards import (
    ArmorChoice,
    equip_armor_list_kb,
    equip_ammo_main_kb,
    equip_ammo_weapon_kb,
    equip_main_kb,
    equip_weapon_pick_kb,
)
from modules.equip.service import EquipError, EquipService, SLOT_TITLE


router = Router()

PAGE_SIZE = 8


def _parse_int(x: str) -> int | None:
    try:
        return int(x)
    except Exception:
        return None


async def _ammo_name(session: AsyncSession, ammo_type_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT name FROM ammo_types WHERE id = :id"),
            {"id": int(ammo_type_id)},
        )
    ).first()
    return str(row[0]) if row else ""


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

    text_out = "\n".join([f"<b>{title}</b>", "Выбери предмет со склада."])

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
        await svc.wear_armor(call.from_user.id, character_id, slot_key, item_id)
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось надеть предмет.", show_alert=True)
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
        from_slots = await svc.weapon_pick_view(call.from_user.id, character_id, int(to_slot))
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть оружие.", show_alert=True)
        return

    text_out = f"<b>Оружие {to_slot}</b>\nВыбери, откуда взять оружие."
    await safe_edit(call, text_out, reply_markup=equip_weapon_pick_kb(character_id, int(to_slot), from_slots))
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
        await svc.move_weapon_between_slots(call.from_user.id, character_id, from_slot, to_slot)
        view = await svc.equipment_view(call.from_user.id, character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось переместить оружие.", show_alert=True)
        return

    await safe_edit(call, svc.equip_text(view), reply_markup=equip_main_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("equip:ammo:open:"))
async def equip_ammo_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    if character_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        view = await svc.equipment_view(call.from_user.id, character_id)
        weapons = await svc.ammo_weapons(call.from_user.id, character_id)
        total = await svc.ammo_total_loaded(character_id)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть аммуницию.", show_alert=True)
        return

    weapons_names: dict[int, str] = {int(k): str(v.get("weapon_name") or "") for k, v in weapons.items()}

    lines = [f"<b>Аммуниция</b> – {view.character_name}", f"Всего на бойце: {total}/{svc.MAX_AMMO_ON_CHARACTER}", ""]

    for slot in (1, 2, 3):
        w = weapons.get(slot)
        if not w:
            continue
        ammo_id, qty = await svc.ammo_slot_state(character_id, slot)
        ammo_line = "не заряжено"
        if ammo_id is not None and qty > 0:
            name = await _ammo_name(db_session, int(ammo_id))
            ammo_line = f"{name} ×{qty}" if name else f"×{qty}"
        lines.append(f"Оружие {slot}: <b>{w['weapon_name']}</b>")
        lines.append(f"Патроны: <b>{ammo_line}</b>")
        lines.append("")

    await safe_edit(call, "\n".join(lines).rstrip(), reply_markup=equip_ammo_main_kb(character_id, weapons_names))
    await call.answer()


async def _render_ammo_weapon(call: CallbackQuery, svc: EquipService, character_id: int, slot: int) -> None:
    weapons = await svc.ammo_weapons(call.from_user.id, character_id)
    if slot not in weapons:
        await call.answer("Оружие в этом слоте не найдено.", show_alert=True)
        return

    w = weapons[slot]
    ammo_id, qty = await svc.ammo_slot_state(character_id, slot)
    total = await svc.ammo_total_loaded(character_id)

    ammo_line = "не заряжено"
    if ammo_id is not None and qty > 0:
        name = await _ammo_name(svc._s, int(ammo_id))
        ammo_line = f"{name} ×{qty}" if name else f"×{qty}"

    ammo_types = await svc.ammo_compatible_types(int(w["caliber_id"]))

    text_out = "\n".join(
        [
            "<b>Аммуниция</b>",
            f"Оружие {slot}: <b>{w['weapon_name']}</b>",
            f"Калибр: <b>{w['caliber_code']}</b>",
            f"Патроны: <b>{ammo_line}</b>",
            f"Всего на бойце: {total}/{svc.MAX_AMMO_ON_CHARACTER}",
        ]
    )

    await safe_edit(
        call,
        text_out,
        reply_markup=equip_ammo_weapon_kb(
            character_id,
            slot,
            ammo_types,
            int(ammo_id) if ammo_id is not None else None,
            qty,
        ),
    )


@router.callback_query(F.data.startswith("equip:ammo:weapon:"))
async def equip_ammo_weapon(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    slot = _parse_int(data[4])
    if character_id is None or slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await _render_ammo_weapon(call, svc, character_id, slot)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось открыть аммуницию.", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("equip:ammo:set:"))
async def equip_ammo_set(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 6:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    slot = _parse_int(data[4])
    ammo_type_id = _parse_int(data[5])
    if character_id is None or slot is None or ammo_type_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.ammo_set_type(call.from_user.id, character_id, slot, ammo_type_id)
        await _render_ammo_weapon(call, svc, character_id, slot)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось зарядить.", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("equip:ammo:add:"))
async def equip_ammo_add(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    slot = _parse_int(data[4])
    if character_id is None or slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.ammo_add(call.from_user.id, character_id, slot)
        await _render_ammo_weapon(call, svc, character_id, slot)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось зарядить.", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("equip:ammo:sub:"))
async def equip_ammo_sub(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    slot = _parse_int(data[4])
    if character_id is None or slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.ammo_sub(call.from_user.id, character_id, slot)
        await _render_ammo_weapon(call, svc, character_id, slot)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось снять.", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("equip:ammo:clear:"))
async def equip_ammo_clear(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = (call.data or "").split(":")
    if len(data) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(data[3])
    slot = _parse_int(data[4])
    if character_id is None or slot is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = EquipService(db_session)
    try:
        await svc.ammo_clear(call.from_user.id, character_id, slot)
        await _render_ammo_weapon(call, svc, character_id, slot)
    except EquipError as e:
        await call.answer(str(e) or "Не удалось снять.", show_alert=True)
        return

    await call.answer()
