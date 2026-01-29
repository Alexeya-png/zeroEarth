from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class StarsWeaponMarketStates(StatesGroup):
    waiting_listing_id = State()

    sell_choose_weapon = State()
    sell_choose_price = State()

    withdraw_choose_listing = State()
