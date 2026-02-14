# keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def weapon_upgrade_pick_weapon_kb(character_id: int, weapons: dict[int, object]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    for slot in sorted(weapons.keys()):
        w = weapons[slot]
        b.add(
            InlineKeyboardButton(
                text=f"Слот {slot}",
                callback_data=f"wup:slot:{character_id}:{slot}",
            )
        )

    b.adjust(3)
    b.row(
        InlineKeyboardButton(text="Назад", callback_data=f"equip:open:{character_id}"),
    )
    return b.as_markup()


def weapon_upgrade_slot_kb(character_id: int, slot: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="Обновить", callback_data=f"wup:slot:{character_id}:{slot}"),
        InlineKeyboardButton(text="Назад", callback_data=f"wup:open:{character_id}"),
    )
    return b.as_markup()
