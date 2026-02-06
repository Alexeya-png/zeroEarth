# modules/common/reply_kb.py
from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def bottom_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Меню")]],
        resize_keyboard=True,
        is_persistent=True,
    )
