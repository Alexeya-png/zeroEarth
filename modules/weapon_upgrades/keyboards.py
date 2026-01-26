from __future__ import annotations

from typing import Mapping

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .service import WeaponRow


def weapon_upgrade_pick_weapon_kb(
    character_id: int,
    weapons: Mapping[int, WeaponRow],
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for slot in sorted(weapons.keys()):
        w = weapons[slot]
        kb.button(text=f"{slot}. {w.name}", callback_data=f"wup:slot:{character_id}:{slot}")
    kb.button(text="К экипировке", callback_data=f"char:eq:{character_id}")
    kb.adjust(1)
    return kb.as_markup()


def weapon_upgrade_slot_kb(character_id: int, slot: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Сменить слот", callback_data=f"wup:open:{character_id}")
    kb.button(text="Обновить", callback_data=f"wup:cancel:{character_id}:{slot}")
    kb.button(text="К экипировке", callback_data=f"char:eq:{character_id}")
    kb.adjust(1)
    return kb.as_markup()
