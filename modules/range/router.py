from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.range.service import RangeService
from modules.start.service import StartService
from modules.start.keyboards import range_kb, char_detail_kb


router = Router()
log = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("range:open:"))
async def range_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad range-open callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        view = await rs.range_view(call.from_user.id, character_id, selected_slot=None)
    except Exception:
        await call.answer("Не удалось открыть тир.", show_alert=True)
        return

    await safe_edit(
        call,
        view["text"],
        reply_markup=range_kb(character_id, view["selected_slot"], view["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:slot:"))
async def range_slot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        log.warning("Bad range-slot callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])
    slot = int(parts[3])

    try:
        view = await rs.range_view(call.from_user.id, character_id, selected_slot=slot)
    except Exception:
        await call.answer("Не удалось переключить оружие.", show_alert=True)
        return

    await safe_edit(
        call,
        view["text"],
        reply_markup=range_kb(character_id, view["selected_slot"], view["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:shoot:"))
async def range_shoot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        log.warning("Bad range-shoot callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])
    slot = int(parts[3])

    try:
        res = await rs.range_shoot(call.from_user.id, character_id, slot=slot, attempts=5)
    except Exception:
        await call.answer("Не удалось выполнить стрельбу.", show_alert=True)
        return

    await safe_edit(
        call,
        res["text"],
        reply_markup=range_kb(character_id, res["selected_slot"], res["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:back:"))
async def range_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        log.warning("Bad range-back callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])

    try:
        text_out = await svc.character_details_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть персонажа.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_detail_kb(character_id))
    await call.answer()
