from __future__ import annotations

import os
from typing import Sequence

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _miniapp_href(start_param: str) -> str:
    bot_username = (os.getenv("TG_BOT_USERNAME") or "").lstrip("@")
    webapp_name = (os.getenv("TG_WEBAPP_NAME") or "").strip().lstrip("/")

    if not bot_username:
        bot_username = "zeroearth_bot"
    if not webapp_name:
        webapp_name = "zeroearth"

    return f"https://t.me/{bot_username}/{webapp_name}?startapp={start_param}"


def stash_kb(
    character_id: int,
    page: int,
    total_pages: int,
    page_links: Sequence[tuple[int, int]] | None = None,  # (номер, item_id) – больше не используем
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    prev_cb = f"stash:page:{character_id}:{page - 1}" if page > 0 else "stash:noop"
    next_cb = f"stash:page:{character_id}:{page + 1}" if page + 1 < total_pages else "stash:noop"

    kb.row(
        InlineKeyboardButton(text="◀️", callback_data=prev_cb),
        InlineKeyboardButton(text=f"{page + 1}/{max(total_pages, 1)}", callback_data="stash:noop"),
        InlineKeyboardButton(text="▶️", callback_data=next_cb),
    )

    kb.row(InlineKeyboardButton(text="Назад к персонажу", callback_data=f"chars:open:{character_id}"))
    kb.row(InlineKeyboardButton(text="Меню", callback_data="menu:back"))

    return kb.as_markup()
