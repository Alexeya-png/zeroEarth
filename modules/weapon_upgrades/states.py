# states.py
from aiogram.fsm.state import StatesGroup, State


class WeaponUpgradeStates(StatesGroup):
    waiting_mod_item_id = State()
