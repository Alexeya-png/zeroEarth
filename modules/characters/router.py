from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.service import StartService
from modules.start.keyboards import (
    main_menu_kb,
    my_chars_kb,
    chars_pick_kb,
    char_detail_kb,
    char_physical_kb,
    char_equipment_kb,
)



router = Router()
log = logging.getLogger(__name__)


@router.callback_query(F.data == "menu:chars")
async def my_characters(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    text_out = await svc.characters_summary_text(call.from_user.id)
    chars = await svc.list_characters(call.from_user.id)
    await safe_edit(call, text_out, reply_markup=my_chars_kb(has_chars=bool(chars)))
    await call.answer()


@router.callback_query(F.data == "chars:pick")
async def pick_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await safe_edit(call, "Персонажей нет.", reply_markup=main_menu_kb())
        await call.answer()
        return

    await safe_edit(call, "Выбор персонажа", reply_markup=chars_pick_kb(chars))
    await call.answer()


@router.callback_query(F.data.startswith("chars:open:"))
async def open_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    try:
        character_id = int((call.data or "").split(":")[-1])
        text_out = await svc.character_details_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть персонажа.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_detail_kb(character_id))
    await call.answer()


@router.callback_query(
    F.data.startswith("char:phys:")
    | F.data.startswith("char:physical:")
    | F.data.startswith("char:state:")
)
async def open_character_physical_state(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad physical-state callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        st = await svc.character_physical_state(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть состояние.", show_alert=True)
        return

    kb = char_physical_kb(character_id)

    if st.image_path:
        try:
            media = InputMediaPhoto(
                media=FSInputFile(st.image_path),
                caption=st.text,
                parse_mode="HTML",
            )
            await call.message.edit_media(media=media, reply_markup=kb)
        except TelegramBadRequest:
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer_photo(
                FSInputFile(st.image_path),
                caption=st.text,
                parse_mode="HTML",
                reply_markup=kb,
            )
    else:
        await safe_edit(call, st.text, reply_markup=kb)

    await call.answer()


@router.callback_query(
    F.data.startswith("char:eq:")
    | F.data.startswith("char:gear:")
    | F.data.startswith("char:equipment:")
)
async def open_character_equipment(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad equipment callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        text_out = await svc.character_equipment_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть снаряжение.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_equipment_kb(character_id))
    await call.answer()


