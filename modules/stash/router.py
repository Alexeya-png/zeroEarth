from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.keyboards import char_stash_kb
from .service import StashService, StashError


router = Router()
log = logging.getLogger(__name__)


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
        text_out = await svc.character_stash_text(call.from_user.id, character_id)
    except StashError as e:
        await call.answer(str(e) or "Не удалось открыть склад.", show_alert=True)
        return
    except Exception:
        await call.answer("Не удалось открыть склад.", show_alert=True)
        return

    try:
        await safe_edit(call, text_out, reply_markup=char_stash_kb(character_id))
    except TelegramBadRequest:
        # fallback: just answer (rare)
        pass

    await call.answer()
