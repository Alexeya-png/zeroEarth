from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


@dataclass(frozen=True)
class ArmorChoice:
    id: int
    name: str
    qty: int
    tier: str
    armor: int
    reliability: int


def equip_main_kb(character_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(text="Шлем", callback_data=f"equip:slot:{character_id}:head:0")
    kb.button(text="Торс", callback_data=f"equip:slot:{character_id}:body:0")
    kb.button(text="Перчатки", callback_data=f"equip:slot:{character_id}:gloves:0")
    kb.button(text="Ботинки", callback_data=f"equip:slot:{character_id}:boots:0")

    kb.button(text="Оружие 1", callback_data=f"equip:wselect:{character_id}:1")
    kb.button(text="Оружие 2", callback_data=f"equip:wselect:{character_id}:2")
    kb.button(text="Оружие 3", callback_data=f"equip:wselect:{character_id}:3")

    kb.button(text="Назад", callback_data=f"char:eq:{character_id}")
    kb.adjust(2)
    return kb.as_markup()


def equip_armor_list_kb(
    character_id: int,
    slot_key: str,
    items: Iterable[ArmorChoice],
    page: int,
    has_prev: bool,
    has_next: bool,
    can_unequip: bool,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for it in items:
        suffix = f" ×{it.qty}" if it.qty > 1 else ""
        tier = f" {it.tier}" if it.tier else ""
        kb.button(
            text=f"{it.name}{suffix}{tier}",
            callback_data=f"equip:wear:{character_id}:{slot_key}:{it.id}",
        )

    nav = []
    if has_prev:
        nav.append(("⬅", f"equip:slot:{character_id}:{slot_key}:{page - 1}"))
    if has_next:
        nav.append(("➡", f"equip:slot:{character_id}:{slot_key}:{page + 1}"))
    for t, cb in nav:
        kb.button(text=t, callback_data=cb)

    if can_unequip:
        kb.button(text="Снять", callback_data=f"equip:unequip:{character_id}:{slot_key}")

    kb.button(text="Назад", callback_data=f"equip:open:{character_id}")

    if nav:
        kb.adjust(1, len(nav), 1, 1)
    else:
        kb.adjust(1)

    return kb.as_markup()


def equip_weapon_pick_kb(character_id: int, to_slot: int, from_slots: dict[int, str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for s in (1, 2, 3):
        if s == to_slot:
            continue
        name = from_slots.get(s)
        if not name:
            continue
        kb.button(text=f"Из слота {s} – {name}", callback_data=f"equip:wmove:{character_id}:{s}:{to_slot}")

    kb.button(text="Назад", callback_data=f"equip:open:{character_id}")
    kb.adjust(1)
    return kb.as_markup()
