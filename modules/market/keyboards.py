# modules/market/keyboards.py
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def market_kb(*, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    nav_row = []
    if has_prev:
        nav_row.append(("←", f"market:page:{page - 1}"))
    if has_next:
        nav_row.append(("→", f"market:page:{page + 1}"))
    for text, cb in nav_row:
        kb.button(text=text, callback_data=cb)

    kb.button(text="⭐ Оружие за Stars", callback_data="wstars:open")
    kb.button(text="Выставить", callback_data="market:sell")
    kb.button(text="Снять с продажи", callback_data="market:withdraw")
    kb.button(text="Меню", callback_data="menu:back")

    if nav_row:
        kb.adjust(len(nav_row), 1, 2, 1)
    else:
        kb.adjust(1, 2, 1)

    return kb.as_markup()


def market_details_kb(*, page: int, listing_id: int | None = None, is_owner: bool | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if listing_id is not None and is_owner is not None:
        if is_owner:
            kb.button(text="Снять с продажи", callback_data=f"market:details:withdraw:{int(listing_id)}:{int(page)}")
        else:
            kb.button(text="Купить", callback_data=f"market:details:buy_confirm:{int(listing_id)}:{int(page)}")
        kb.adjust(1)

    kb.button(text="Назад", callback_data=f"market:page:{int(page)}")
    kb.adjust(1)
    return kb.as_markup()


def market_buy_confirm_kb(*, listing_id: int, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить", callback_data=f"market:details:buy:{int(listing_id)}:{int(page)}")
    kb.button(text="Назад", callback_data=f"market:details:show:{int(listing_id)}:{int(page)}")
    kb.adjust(1, 1)
    return kb.as_markup()


def market_pick_char_kb(chars: list[dict], *, action: str, listing_id: int, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for ch in chars:
        cid = int(ch.get("id") or 0)
        name = str(ch.get("name") or f"#{cid}")
        if cid <= 0:
            continue
        kb.button(
            text=name,
            callback_data=f"market:details:{action}:char:{int(listing_id)}:{int(page)}:{cid}",
        )

    kb.adjust(1)
    kb.button(text="Назад", callback_data=f"market:details:show:{int(listing_id)}:{int(page)}")
    kb.adjust(1)
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
