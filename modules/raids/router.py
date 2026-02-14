# modules/raids/router.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.keyboards import main_menu_kb, chars_pick_kb
from modules.start.service import StartService

from modules.equip.service import EquipService
from modules.equip.keyboards import equip_main_kb

from modules.raids.service import RaidsService
from modules.raids.keyboards import (
    raids_map_kb,
    raids_behavior_kb,
    raids_confirm_kb,
    raids_status_kb,
    raids_back_to_equip_kb,
)

router = Router()


async def _has_active_raid(db_session: AsyncSession, character_id: int) -> bool:
    q = await db_session.execute(
        text("SELECT 1 FROM raids WHERE character_id = :cid AND status = 'active' LIMIT 1"),
        {"cid": int(character_id)},
    )
    return q.scalar() is not None


async def _open_raid_status(call: CallbackQuery, db_session: AsyncSession, character_id: int) -> None:
    text_out, notify_enabled = await RaidsService(db_session).raid_status_text(call.from_user.id, int(character_id))
    await safe_edit(call, text_out, reply_markup=raids_status_kb(character_id=int(character_id), notify_enabled=notify_enabled))


async def _open_equip_raid(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, character_id: int) -> None:
    await state.clear()
    await state.update_data(raid_mode=True, raid_character_id=int(character_id))

    svc = EquipService(db_session)
    view = await svc.equipment_view(call.from_user.id, int(character_id))
    text_out = svc.equip_text(view)
    await safe_edit(call, text_out, reply_markup=equip_main_kb(int(character_id), raid_mode=True))


async def _render_map(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, character_id: int) -> None:
    raids = RaidsService(db_session)
    err = await raids.get_character_gate_error(call.from_user.id, int(character_id))
    if err:
        await call.answer(err, show_alert=True)
        return

    locations = await raids.list_locations()
    data = await state.get_data()
    selected_location_id = data.get("raid_location_id")
    selected_behavior = data.get("raid_behavior_model")

    await safe_edit(
        call,
        "<b>Карта</b>\nВыбери локацию и поведение.",
        reply_markup=raids_map_kb(
            character_id=int(character_id),
            locations=locations,
            selected_location_id=int(selected_location_id) if selected_location_id is not None else None,
            selected_behavior=str(selected_behavior) if selected_behavior else None,
        ),
    )


@router.callback_query(F.data == "menu:raids")
async def menu_raids(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)

    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await safe_edit(call, "У тебя нет персонажей.", reply_markup=main_menu_kb())
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        if await _has_active_raid(db_session, cid):
            await _open_raid_status(call, db_session, cid)
        else:
            raids = RaidsService(db_session)
            text_out, _ = await raids.raid_status_text(call.from_user.id, cid, include_last_result=True)
            if text_out.startswith("<b>Результат рейда</b>"):
                await safe_edit(call, text_out, reply_markup=raids_back_to_equip_kb(character_id=cid))
            else:
                await _open_equip_raid(call, db_session, state, cid)
        await call.answer()
        return

    await safe_edit(
        call,
        "<b>Рейды</b>\nВыбери персонажа.",
        reply_markup=chars_pick_kb(chars, item_cb_prefix="raids:pick_char", show_create=False),
    )
    await call.answer()


@router.callback_query(F.data.startswith("raids:pick_char:"))
async def raids_pick_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    if await _has_active_raid(db_session, character_id):
        await state.clear()
        await _open_raid_status(call, db_session, character_id)
    else:
        raids = RaidsService(db_session)
        text_out, _ = await raids.raid_status_text(call.from_user.id, character_id, include_last_result=True)
        if text_out.startswith("<b>Результат рейда</b>"):
            await state.clear()
            await safe_edit(call, text_out, reply_markup=raids_back_to_equip_kb(character_id=character_id))
        else:
            await _open_equip_raid(call, db_session, state, character_id)
    await call.answer()


@router.callback_query(F.data.startswith("raids:map:"))
async def raids_open_map(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    await _render_map(call, db_session, state, character_id)
    await call.answer()


@router.callback_query(F.data.startswith("raids:loc:"))
async def raids_choose_location(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    try:
        _, _, cid, loc = call.data.split(":", 3)
        character_id = int(cid)
        location_id = int(loc)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    await state.update_data(raid_location_id=location_id)
    await _render_map(call, db_session, state, character_id)
    await call.answer()


@router.callback_query(F.data.startswith("raids:beh:") & ~F.data.startswith("raids:beh:set:"))
async def raids_open_behavior(call: CallbackQuery, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    data = await state.get_data()
    selected = data.get("raid_behavior_model")
    await safe_edit(
        call,
        "<b>Поведение</b>\nВыбери модель поведения.",
        reply_markup=raids_behavior_kb(character_id=character_id, selected=str(selected) if selected else None),
    )
    await call.answer()


@router.callback_query(F.data.startswith("raids:beh:set:"))
async def raids_set_behavior(call: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    try:
        _, _, _, cid, beh = call.data.split(":", 4)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    await state.update_data(raid_behavior_model=str(beh))
    await _render_map(call, db_session, state, character_id)
    await call.answer()


@router.callback_query(F.data.startswith("raids:confirm:"))
async def raids_confirm(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    data = await state.get_data()
    location_id = data.get("raid_location_id")
    behavior = str(data.get("raid_behavior_model") or "aggressive")

    if location_id is None:
        await call.answer("Сначала выбери локацию.", show_alert=True)
        await _render_map(call, db_session, state, character_id)
        return

    raids = RaidsService(db_session)
    locations = await raids.list_locations()
    name = None
    for loc in locations:
        if int(loc.get("id")) == int(location_id):
            name = str(loc.get("name") or f"Локация {int(location_id)}")
            break
    if name is None:
        name = f"Локация {int(location_id)}"

    beh_name = "Агрессивно" if behavior == "aggressive" else "Осторожно"
    await safe_edit(
        call,
        f"<b>Подтверждение</b>\nЛокация – {name}\nПоведение – {beh_name}",
        reply_markup=raids_confirm_kb(character_id=character_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("raids:start:"))
async def raids_start(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    data = await state.get_data()
    location_id = data.get("raid_location_id")
    behavior = str(data.get("raid_behavior_model") or "aggressive")
    goal = "any"

    if location_id is None:
        await call.answer("Сначала выбери локацию.", show_alert=True)
        await _render_map(call, db_session, state, character_id)
        return

    raids = RaidsService(db_session)
    res = await raids.start_raid(call.from_user.id, character_id, int(location_id), behavior, goal)
    if not res.ok:
        await call.answer(res.message, show_alert=True)
        return

    text_out, notify_enabled = await raids.raid_status_text(call.from_user.id, character_id)
    await safe_edit(call, text_out, reply_markup=raids_status_kb(character_id=character_id, notify_enabled=notify_enabled))
    await call.answer()


@router.callback_query(F.data.startswith("raids:status:"))
async def raids_status(call: CallbackQuery, db_session: AsyncSession):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    text_out, notify_enabled = await RaidsService(db_session).raid_status_text(
        call.from_user.id,
        character_id,
        include_last_result=True,
    )

    if text_out.startswith("<b>Рейд</b>"):
        kb = raids_status_kb(character_id=character_id, notify_enabled=notify_enabled)
    else:
        kb = raids_back_to_equip_kb(character_id=character_id)

    await safe_edit(call, text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("raids:notify:"))
async def raids_notify_toggle(call: CallbackQuery, db_session: AsyncSession):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    raids = RaidsService(db_session)
    try:
        enabled = await raids.toggle_notifications(call.from_user.id, character_id)
    except ValueError as e:
        await call.answer(str(e), show_alert=True)
        return

    text_out, notify_enabled = await raids.raid_status_text(call.from_user.id, character_id)
    await safe_edit(call, text_out, reply_markup=raids_status_kb(character_id=character_id, notify_enabled=notify_enabled))
    await call.answer("Уведомления включены" if enabled else "Уведомления выключены")


@router.callback_query(F.data.startswith("raids:cancel:"))
async def raids_cancel(call: CallbackQuery, db_session: AsyncSession):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    ok, msg = await RaidsService(db_session).cancel_raid(call.from_user.id, character_id)
    if not ok:
        await call.answer(msg, show_alert=True)
        return

    await safe_edit(call, f"<b>Рейды</b>\n{msg}", reply_markup=raids_back_to_equip_kb(character_id=character_id))
    await call.answer()


@router.callback_query(F.data.startswith("raids:back_equip:"))
async def raids_back_to_equip(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        _, _, cid = call.data.split(":", 2)
        character_id = int(cid)
    except Exception:
        await call.answer("Ошибка.", show_alert=True)
        return

    await _open_equip_raid(call, db_session, state, character_id)
    await call.answer()
