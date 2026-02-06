from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def raids_map_kb(
    *,
    character_id: int,
    locations: list[dict],
    selected_location_id: int | None,
    selected_behavior: str | None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for loc in locations:
        loc_id = int(loc["id"])
        name = str(loc.get("name") or f"Локация {loc_id}")
        mark = "● " if (selected_location_id is not None and loc_id == int(selected_location_id)) else ""
        kb.button(text=f"{mark}{name}", callback_data=f"raids:loc:{character_id}:{loc_id}")

    beh_text = "Поведение"
    if selected_behavior == "aggressive":
        beh_text = "Поведение – ● Агрессивно"
    elif selected_behavior == "stealth":
        beh_text = "Поведение – ● Осторожно"

    kb.button(text=beh_text, callback_data=f"raids:beh:{character_id}")
    kb.button(text="Старт", callback_data=f"raids:confirm:{character_id}")
    kb.button(text="Назад", callback_data=f"raids:back_equip:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")

    sizes = [1] * len(locations) + [2, 2]
    kb.adjust(*sizes)
    return kb.as_markup()


def raids_behavior_kb(*, character_id: int, selected: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    options = [
        ("aggressive", "Агрессивно"),
        ("stealth", "Осторожно"),
    ]

    for code, name in options:
        mark = "● " if (selected is not None and str(selected) == code) else ""
        kb.button(text=f"{mark}{name}", callback_data=f"raids:beh:set:{character_id}:{code}")

    kb.button(text="Назад", callback_data=f"raids:map:{character_id}")
    kb.adjust(1)
    return kb.as_markup()


def raids_confirm_kb(*, character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Начать рейд", callback_data=f"raids:start:{character_id}")
    kb.button(text="Назад", callback_data=f"raids:map:{character_id}")
    kb.adjust(1)
    return kb.as_markup()


def raids_status_kb(*, character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Статус", callback_data=f"raids:status:{character_id}")
    kb.button(text="Отмена", callback_data=f"raids:cancel:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(2)
    return kb.as_markup()


def raids_back_to_equip_kb(*, character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Снаряжение", callback_data=f"equip:open_raid:{character_id}")
    kb.button(text="Меню", callback_data="menu:back")
    kb.adjust(2)
    return kb.as_markup()
