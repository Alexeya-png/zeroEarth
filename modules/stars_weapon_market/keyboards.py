from __future__ import annotations

from typing import Iterable, Mapping, Any

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def stars_weapon_market_kb(*, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    nav_row = []
    if has_prev:
        nav_row.append(("← Назад", f"wstars:page:{page - 1}"))
    if has_next:
        nav_row.append(("Вперёд →", f"wstars:page:{page + 1}"))

    for text, cb in nav_row:
        kb.button(text=text, callback_data=cb)

    kb.button(text="Выставить оружие", callback_data="wstars:sell")
    kb.button(text="Снять с продажи", callback_data="wstars:withdraw")
    kb.button(text="Меню", callback_data="menu:back")

    if nav_row:
        kb.adjust(len(nav_row), 2, 1)
    else:
        kb.adjust(2, 1)

    return kb.as_markup()


def stars_weapon_details_kb(*, page: int, listing_id: int, can_buy: bool, price_stars: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if can_buy:
        kb.button(text=f"Купить ⭐{max(0, int(price_stars))}", callback_data=f"wstars:buy:{listing_id}:{page}")

    kb.button(text="Назад", callback_data=f"wstars:page:{page}")
    kb.button(text="Меню", callback_data="menu:back")

    if can_buy:
        kb.adjust(1, 2)
    else:
        kb.adjust(1, 1)

    return kb.as_markup()


def cancel_to_market_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="wstars:open")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1, 1)
    return kb.as_markup()


def pick_character_kb(
    characters: Iterable[Mapping[str, Any]],
    *,
    item_cb_prefix: str,
    cancel_cb: str = "wstars:open",
    cancel_text: str = "Отмена",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in characters:
        cid = int(ch["id"])
        name = str(ch.get("name") or "Без имени")
        kb.button(text=f"#{cid} – {name}", callback_data=f"{item_cb_prefix}:{cid}")
    kb.button(text=cancel_text, callback_data=cancel_cb)
    kb.adjust(1)
    return kb.as_markup()


def pick_buy_character_kb(
    characters: Iterable[Mapping[str, Any]],
    *,
    listing_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in characters:
        cid = int(ch["id"])
        name = str(ch.get("name") or "Без имени")
        kb.button(text=f"#{cid} – {name}", callback_data=f"wstars:buyc:{listing_id}:{cid}:{page}")
    kb.button(text="Назад", callback_data=f"wstars:details:{listing_id}:{page}")
    kb.adjust(1)
    return kb.as_markup()
