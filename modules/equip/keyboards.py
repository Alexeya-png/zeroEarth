# modules/equip/keyboards.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any

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


@dataclass(frozen=True)
class WeaponChoice:
    id: int
    name: str
    qty: int
    tier: str
    equipped_slot: int | None


def _open_cb(character_id: int, raid_mode: bool) -> str:
    return f"equip:open_raid:{int(character_id)}" if raid_mode else f"equip:open:{int(character_id)}"


def equip_main_kb(character_id: int, raid_mode: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(text="Шлем", callback_data=f"equip:slot:{character_id}:head:0")
    kb.button(text="Торс", callback_data=f"equip:slot:{character_id}:body:0")
    kb.button(text="Перчатки", callback_data=f"equip:slot:{character_id}:gloves:0")
    kb.button(text="Ботинки", callback_data=f"equip:slot:{character_id}:boots:0")

    kb.button(text="Оружие 1", callback_data=f"equip:wselect:{character_id}:1")
    kb.button(text="Оружие 2", callback_data=f"equip:wselect:{character_id}:2")
    kb.button(text="Оружие 3", callback_data=f"equip:wselect:{character_id}:3")

    kb.button(text="Аммуниция", callback_data=f"equip:ammo:open:{character_id}")

    if raid_mode:
        kb.button(text="К карте", callback_data=f"raids:map:{character_id}")
        kb.button(text="Меню", callback_data="menu:back")

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
    raid_mode: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for it in items:
        suffix = f" ×{it.qty}" if int(it.qty) > 1 else ""
        tier = f" {it.tier}" if str(it.tier).strip() else ""
        kb.button(
            text=f"{it.name}{suffix}{tier}",
            callback_data=f"equip:wear:{character_id}:{slot_key}:{it.id}",
        )

    if has_prev:
        kb.button(text="⬅", callback_data=f"equip:slot:{character_id}:{slot_key}:{int(page) - 1}")
    if has_next:
        kb.button(text="➡", callback_data=f"equip:slot:{character_id}:{slot_key}:{int(page) + 1}")

    if can_unequip:
        kb.button(text="Снять", callback_data=f"equip:unequip:{character_id}:{slot_key}")

    kb.button(text="Назад", callback_data=_open_cb(character_id, raid_mode))

    kb.adjust(1)
    return kb.as_markup()


def equip_weapon_list_kb(
    character_id: int,
    to_slot: int,
    weapons: Iterable[WeaponChoice],
    page: int,
    has_prev: bool,
    has_next: bool,
    can_unequip: bool,
    raid_mode: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for it in weapons:
        if int(it.id) <= 0:
            continue
        suffix = f" ×{it.qty}" if int(it.qty) > 1 else ""
        tier = f" {it.tier}" if str(it.tier).strip() else ""

        mark = ""
        if it.equipped_slot is not None:
            if int(it.equipped_slot) == int(to_slot):
                mark = "● "
            else:
                mark = f"{int(it.equipped_slot)}→ "

        kb.button(
            text=f"{mark}{it.name}{suffix}{tier}",
            callback_data=f"equip:wequip:{character_id}:{int(to_slot)}:{int(it.id)}",
        )

    if has_prev:
        kb.button(text="⬅", callback_data=f"equip:wselect:{character_id}:{int(to_slot)}:{int(page) - 1}")
    if has_next:
        kb.button(text="➡", callback_data=f"equip:wselect:{character_id}:{int(to_slot)}:{int(page) + 1}")

    if can_unequip:
        kb.button(text="Снять", callback_data=f"equip:wunequip:{character_id}:{int(to_slot)}")

    kb.button(text="Назад", callback_data=_open_cb(character_id, raid_mode))

    kb.adjust(1)
    return kb.as_markup()


def equip_weapon_pick_kb(
    character_id: int,
    to_slot: int,
    from_slots: Mapping[int, str],
    raid_mode: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for s in (1, 2, 3):
        if int(s) == int(to_slot):
            continue
        name = (from_slots or {}).get(int(s))
        if not name:
            continue
        kb.button(text=f"Из слота {s} – {name}", callback_data=f"equip:wmove:{character_id}:{s}:{to_slot}")

    kb.button(text="Назад", callback_data=_open_cb(character_id, raid_mode))
    kb.adjust(1)
    return kb.as_markup()


def equip_ammo_main_kb(character_id: int, weapons_names: Mapping[int, str], raid_mode: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for slot in (1, 2, 3):
        name = (weapons_names or {}).get(int(slot), "")
        if not name:
            continue
        kb.button(text=f"Оружие {slot}", callback_data=f"equip:ammo:weapon:{character_id}:{slot}")

    kb.button(text="Назад", callback_data=_open_cb(character_id, raid_mode))
    kb.adjust(1)
    return kb.as_markup()


def equip_ammo_weapon_kb(
    character_id: int,
    slot: int,
    ammo_types: list[Mapping[str, Any]],
    selected_ammo_type_id: int | None,
    equipped_qty: int,
    raid_mode: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for a in ammo_types:
        aid = int(a.get("id") or 0)
        name = str(a.get("name") or "Боеприпасы")
        mark = "● " if (selected_ammo_type_id is not None and aid == int(selected_ammo_type_id)) else ""
        kb.button(text=f"{mark}{name}", callback_data=f"equip:ammo:set:{character_id}:{int(slot)}:{aid}")

    if selected_ammo_type_id is not None:
        kb.button(text="➕", callback_data=f"equip:ammo:add:{character_id}:{int(slot)}")

    if int(equipped_qty) > 0:
        kb.button(text="➖", callback_data=f"equip:ammo:sub:{character_id}:{int(slot)}")

    if selected_ammo_type_id is not None or int(equipped_qty) > 0:
        kb.button(text="Снять", callback_data=f"equip:ammo:clear:{character_id}:{int(slot)}")

    kb.button(text="Назад", callback_data=f"equip:ammo:open:{character_id}")
    kb.button(text="Снаряжение", callback_data=_open_cb(character_id, raid_mode))

    kb.adjust(1)
    return kb.as_markup()
