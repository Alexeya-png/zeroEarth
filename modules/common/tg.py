from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


async def safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def cb_last_int(data: str | None) -> int | None:
    if not data:
        return None
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        return None
    return int(parts[-1])
