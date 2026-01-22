from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery


router = Router()
log = logging.getLogger(__name__)


@router.callback_query()
async def unknown_callback(call: CallbackQuery):
    log.warning("Unhandled callback_data: %r", call.data)
    await call.answer("Кнопка не распознана.", show_alert=True)
