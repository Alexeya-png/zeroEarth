from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.service import StartService, CreateCharacterError
from modules.start.keyboards import main_menu_kb, create_menu_kb, cancel_create_kb
from modules.start.states import CreateCharacterFlow


router = Router()


@router.message(CommandStart())
async def start(message: Message, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(message.from_user.id)
    await message.answer("Меню", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:back")
async def menu_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)
    await safe_edit(call, "Меню", reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:create")
async def create_menu(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    user = await svc.ensure_user(call.from_user.id)
    await safe_edit(
        call,
        "Создание персонажа – выбери тип",
        reply_markup=create_menu_kb(is_premium=(user.account_tier == "premium")),
    )
    await call.answer()


@router.callback_query(F.data.in_({"create:free", "create:premium"}))
async def create_choose_type(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    svc = StartService(db_session)
    user = await svc.ensure_user(call.from_user.id)

    creation_type = "free" if call.data == "create:free" else "premium"
    if creation_type == "premium" and user.account_tier != "premium":
        await call.answer("Нужен premium аккаунт.", show_alert=True)
        return

    await state.set_state(CreateCharacterFlow.waiting_name)
    await state.update_data(creation_type=creation_type)

    await safe_edit(call, "Введи имя персонажа одним сообщением.", reply_markup=cancel_create_kb())
    await call.answer()


@router.callback_query(F.data == "create:cancel")
async def create_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call, "Меню", reply_markup=main_menu_kb())
    await call.answer()


@router.message(CreateCharacterFlow.waiting_name)
async def create_name_input(message: Message, db_session: AsyncSession, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя пустое. Введи имя одним сообщением.", reply_markup=cancel_create_kb())
        return
    if len(name) > 24:
        await message.answer("Слишком длинное имя. До 24 символов.", reply_markup=cancel_create_kb())
        return

    data = await state.get_data()
    creation_type = str(data.get("creation_type", "free"))

    loading = await message.answer("Создаю персонажа – инициализация…")
    await asyncio.sleep(0.6)
    await loading.edit_text("Создаю персонажа – распределяю характеристики…")
    await asyncio.sleep(0.6)
    await loading.edit_text("Создаю персонажа – выдаю стартовое снаряжение…")

    svc = StartService(db_session)
    try:
        summary = await svc.create_character(message.from_user.id, creation_type, name)
    except CreateCharacterError as e:
        await loading.edit_text(str(e), reply_markup=main_menu_kb())
        await state.clear()
        return

    await asyncio.sleep(0.4)
    await loading.edit_text(summary, reply_markup=main_menu_kb(), parse_mode="HTML")
    await state.clear()
