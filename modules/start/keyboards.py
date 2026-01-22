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
    kb.adjust(1)
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
    kb.button(text="Физическое состояние", callback_data=f"char:phys:{character_id}")
    kb.button(text="Снаряжение", callback_data=f"char:eq:{character_id}")
    kb.button(text="Тир", callback_data=f"range:open:{character_id}")
    kb.button(text="Назад к списку", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def char_physical_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад к персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Назад к списку", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()


def char_equipment_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад к персонажу", callback_data=f"chars:open:{character_id}")
    kb.button(text="Назад к списку", callback_data="chars:pick")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(1)
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
