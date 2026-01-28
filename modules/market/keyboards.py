from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def market_kb(*, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    nav_row = []
    if has_prev:
        nav_row.append(("← Назад", f"market:page:{page - 1}"))
    if has_next:
        nav_row.append(("Вперёд →", f"market:page:{page + 1}"))
    for text, cb in nav_row:
        kb.button(text=text, callback_data=cb)

    kb.button(text="Выставить", callback_data="market:sell")
    kb.button(text="Снять с продажи", callback_data="market:withdraw")
    kb.button(text="Меню", callback_data="menu:back")

    if nav_row:
        kb.adjust(len(nav_row), 2, 1)
    else:
        kb.adjust(2, 1)

    return kb.as_markup()


def market_details_kb(*, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=f"market:page:{page}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1, 1)
    return kb.as_markup()


def market_sell_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="market:sell:cancel")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1, 1)
    return kb.as_markup()


def market_withdraw_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="market:withdraw:cancel")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1, 1)
    return kb.as_markup()
