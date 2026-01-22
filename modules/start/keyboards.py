from __future__ import annotations

from typing import Iterable, Mapping, Any

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Мои персонажи", callback_data="menu:chars")
    kb.button(text="Создать персонажа", callback_data="menu:create")
    kb.adjust(1)
    return kb.as_markup()


def create_menu_kb(is_premium: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Free", callback_data="create:free")
    kb.button(
        text="Premium" if is_premium else "Premium – нужен premium",
        callback_data="create:premium",
    )
    kb.button(text="Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def cancel_create_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="create:cancel")
    return kb.as_markup()


def my_chars_kb(has_chars: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_chars:
        kb.button(text="Выбор персонажа", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def chars_pick_kb(characters: Iterable[Mapping[str, Any]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in characters:
        cid = int(ch["id"])
        name = str(ch["name"] or "Без имени")
        kb.button(text=f"#{cid} – {name}", callback_data=f"chars:open:{cid}")
    kb.button(text="Назад", callback_data="menu:chars")
    kb.adjust(1)
    return kb.as_markup()


def char_detail_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад к списку", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()
