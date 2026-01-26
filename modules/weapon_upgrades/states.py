from aiogram.fsm.state import State, StatesGroup


class WeaponUpgradeStates(StatesGroup):
    waiting_mod_item_id = State()
