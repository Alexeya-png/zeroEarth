from aiogram.fsm.state import State, StatesGroup


class CreateCharacterFlow(StatesGroup):
    waiting_name = State()
