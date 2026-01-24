from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def stash_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад к персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Назад к списку", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()
