from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from .keyboards import stash_kb
from .service import StashService, StashError


router = Router()
log = logging.getLogger(__name__)

PAGE_SIZE = 15


@router.callback_query(F.data == "stash:noop")
async def stash_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("stash:page:"))
async def stash_page(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = call.data or ""
    parts = data.split(":")
    # stash:page:{cid}:{page}
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].lstrip("-").isdigit():
        log.warning("Bad stash page callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])
    page = int(parts[3])

    svc = StashService(db_session)
    try:
        res = await svc.character_stash_page(call.from_user.id, character_id, page=page, page_size=PAGE_SIZE)
    except StashError as e:
        await call.answer(str(e) or "Не удалось открыть склад.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть склад.", show_alert=True)
        return

    try:
        await safe_edit(
            call,
            res.text,
            reply_markup=stash_kb(character_id, res.page, res.total_pages, res.page_links),
        )
    except TelegramBadRequest:
        pass

    await call.answer()


@router.callback_query(
    F.data.startswith("char:stash:")
    | F.data.startswith("char:storage:")
    | F.data.startswith("char:warehouse:")
)
async def open_character_stash(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad stash callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    svc = StashService(db_session)
    try:
        res = await svc.character_stash_page(call.from_user.id, character_id, page=0, page_size=PAGE_SIZE)
    except StashError as e:
        await call.answer(str(e) or "Не удалось открыть склад.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть склад.", show_alert=True)
        return

    try:
        await safe_edit(
            call,
            res.text,
            reply_markup=stash_kb(character_id, res.page, res.total_pages, res.page_links),
        )
    except TelegramBadRequest:
        pass

    await call.answer()
