from __future__ import annotations

import os
from typing import Iterable, Mapping, Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _miniapp_href(start_param: str | None = None) -> str:
    bot_username = (os.getenv("TG_BOT_USERNAME") or "").lstrip("@")
    webapp_name = (os.getenv("TG_WEBAPP_NAME") or "").strip().lstrip("/")

    if not bot_username:
        bot_username = "zeroearth_bot"

    # Для кнопки в меню используем main mini app.
    # TG_WEBAPP_NAME оставляем для совместимости, но не используем, чтобы не ловить "веб-приложение не найдено".
    _ = webapp_name
    base = f"https://t.me/{bot_username}"

    sp = (start_param or "").strip() or "stash"
    return f"{base}?startapp={quote(sp)}"


def _webapp_public_url(webapp_character_id: int | None = None) -> str | None:
    base = (
        os.getenv("WEBAPP_PUBLIC_URL")
        or os.getenv("WEBAPP_URL")
        or os.getenv("TG_WEBAPP_URL")
        or ""
    ).strip()
    if not base:
        return None

    if not (base.startswith("https://") or base.startswith("http://")):
        base = "https://" + base

    p = urlsplit(base)
    path = p.path or "/"
    q = dict(parse_qsl(p.query, keep_blank_values=True))

    if webapp_character_id is not None:
        try:
            cid = int(webapp_character_id)
        except Exception:
            cid = 0
        if cid > 0:
            q["cid"] = str(cid)

    return urlunsplit((p.scheme, p.netloc, path, urlencode(q), p.fragment))


def main_menu_kb(webapp_character_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Персонаж", callback_data="menu:char")
    kb.button(text="Склад", callback_data="menu:stash")
    start_param = ""
    if webapp_character_id is not None:
        try:
            cid = int(webapp_character_id)
        except Exception:
            cid = 0
        if cid > 0:
            start_param = f"c{cid}"

    kb.button(text="Веб приложение", url=_miniapp_href(start_param))
    kb.button(text="Рынок", callback_data="menu:market")
    kb.button(text="Рейды", callback_data="menu:raids")
    kb.button(text="Квесты", callback_data="menu:quests")
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
    kb.adjust(1)
    return kb.as_markup()


def my_chars_kb(has_chars: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_chars:
        kb.button(text="Выбор персонажа", callback_data="chars:pick")
    kb.button(text="Создать персонажа", callback_data="menu:create")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def chars_pick_kb(
    characters: Iterable[Mapping[str, Any]],
    *,
    item_cb_prefix: str = "chars:open",
    show_create: bool = True,
    create_cb: str = "menu:create",
    show_menu: bool = True,
    menu_cb: str = "menu:back",
    menu_text: str = "Меню",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for ch in characters:
        cid = int(ch["id"])
        name = str(ch["name"] or "Без имени")
        kb.button(text=f"#{cid} – {name}", callback_data=f"{item_cb_prefix}:{cid}")

    if show_create:
        kb.button(text="Создать персонажа", callback_data=create_cb)

    if show_menu:
        kb.button(text=menu_text, callback_data=menu_cb)

    kb.adjust(1)
    return kb.as_markup()


def char_detail_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Физическое состояние", callback_data=f"char:phys:{character_id}")
    kb.button(text="Снаряжение", callback_data=f"char:eq:{character_id}")
    kb.button(text="Тир", callback_data=f"range:open:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(2)
    return kb.as_markup()


def char_physical_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад к персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def char_equipment_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Снарядить бойца", callback_data=f"equip:open:{character_id}")
    kb.button(text="Улучшить оружие", callback_data=f"wup:open:{character_id}")
    kb.button(text="Тест: бой", callback_data=f"clash:test:{character_id}")
    kb.button(text="Назад к персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def clash_pick_enemy_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Манекен", callback_data=f"clash:run:{character_id}:mannequin")
    kb.button(text="Рейдер", callback_data=f"clash:run:{character_id}:raider")
    kb.button(text="Охранник", callback_data=f"clash:run:{character_id}:guard")
    kb.button(text="Назад", callback_data=f"char:eq:{character_id}")
    kb.adjust(1)
    return kb.as_markup()


def clash_result_kb(character_id: int, enemy_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Повторить", callback_data=f"clash:run:{character_id}:{enemy_key}")
    kb.button(text="Выбрать другого", callback_data=f"clash:test:{character_id}")
    kb.button(text="Назад", callback_data=f"char:eq:{character_id}")
    kb.adjust(1)
    return kb.as_markup()


def char_stash_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="К персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(2)
    return kb.as_markup()


def range_kb(character_id: int, selected_slot: int, slots: Mapping[int, bool]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    slot_buttons = []
    for s in (1, 2, 3):
        if not slots.get(s):
            continue
        mark = " ✅" if s == selected_slot else ""
        slot_buttons.append((f"Оружие {s}{mark}", f"range:slot:{character_id}:{s}"))

    for text_, cb in slot_buttons:
        kb.button(text=text_, callback_data=cb)

    if slot_buttons:
        kb.adjust(len(slot_buttons))
    else:
        kb.adjust(1)

    kb.button(text="Выстрелить ×5", callback_data=f"range:shoot:{character_id}:{selected_slot}")
    kb.button(text="Назад", callback_data=f"range:back:{character_id}")
    kb.adjust(1)
    return kb.as_markup()
