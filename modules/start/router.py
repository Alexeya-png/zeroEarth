from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.service import StartService, CreateCharacterError
from modules.stash.service import StashService, StashError
from modules.start.keyboards import (
    main_menu_kb,
    create_menu_kb,
    cancel_create_kb,
    chars_pick_kb,
    char_detail_kb,
    char_stash_kb,
)
from modules.start.states import CreateCharacterFlow


router = Router()


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _menu_text(first_name: str | None) -> str:
    name = (first_name or "").strip()
    if name:
        return f"Привет, {_esc_html(name)}.\nВыбери раздел:"
    return "Привет.\nВыбери раздел:"


@router.message(CommandStart())
async def start(message: Message, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(message.from_user.id)
    await message.answer(_menu_text(message.from_user.first_name), reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:back")
async def menu_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)
    await safe_edit(call, _menu_text(call.from_user.first_name), reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:char")
async def menu_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)

    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await safe_edit(call, "У тебя нет персонажей.", reply_markup=chars_pick_kb([]))
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        try:
            text_out = await svc.character_details_text(tg_id=call.from_user.id, character_id=cid)
        except Exception:
            await call.answer("Персонаж не найден.", show_alert=True)
            return

        await safe_edit(call, text_out, reply_markup=char_detail_kb(cid))
        await call.answer()
        return

    await safe_edit(
        call,
        "<b>Выбор персонажа</b>\nНажми на персонажа.",
        reply_markup=chars_pick_kb(chars),
    )
    await call.answer()


@router.callback_query(F.data == "menu:stash")
async def menu_stash(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)

    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await safe_edit(call, "У тебя нет персонажей.", reply_markup=chars_pick_kb([]))
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        stash = StashService(db_session)
        try:
            text_out = await stash.character_stash_text(call.from_user.id, cid)
        except StashError as e:
            await call.answer(str(e) or "Не удалось открыть склад.", show_alert=True)
            return
        except Exception:
            await call.answer("Не удалось открыть склад.", show_alert=True)
            return

        await safe_edit(call, text_out, reply_markup=char_stash_kb(cid))
        await call.answer()
        return

    await safe_edit(
        call,
        "<b>Склад</b>\nВыбери персонажа.",
        reply_markup=chars_pick_kb(chars, item_cb_prefix="char:stash", show_create=False),
    )
    await call.answer()


@router.callback_query(F.data == "menu:market")
async def menu_market(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call, "<b>Рынок</b>\nВ разработке.", reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:quests")
async def menu_quests(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call, "<b>Квесты</b>\nВ разработке.", reply_markup=main_menu_kb())
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
    await safe_edit(call, _menu_text(call.from_user.first_name), reply_markup=main_menu_kb())
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
