from __future__ import annotations

from aiogram.fsm.state import StatesGroup, State


class MarketStates(StatesGroup):
    waiting_listing_id = State()

    buy_choose_qty = State()

    sell_choose_item = State()
    sell_choose_qty = State()
    sell_choose_price = State()

    withdraw_choose_listing = State()
