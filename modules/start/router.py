from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit, safe_delete
from modules.start.service import StartService, CreateCharacterError
from modules.stash.service import StashService, StashError
from modules.stash.keyboards import stash_kb
from modules.start.keyboards import (
    main_menu_kb,
    create_menu_kb,
    cancel_create_kb,
    chars_pick_kb,
    char_detail_kb,
)
from modules.start.states import CreateCharacterFlow

from modules.market.service import MarketService
from modules.market.keyboards import market_kb
from modules.market.states import MarketStates

MARKET_PAGE_SIZE = 30

router = Router()


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _menu_text(first_name: str | None) -> str:
    name = (first_name or "").strip()
    if name:
        return f"Привет, {_esc_html(name)}.\nВыбери раздел:"
    return "Привет.\nВыбери раздел:"


async def _menu_markup(db_session: AsyncSession, tg_id: int):
    svc = StartService(db_session)
    user = await svc.ensure_user(int(tg_id))

    cid: int | None = None
    try:
        row = (
            await db_session.execute(
                text(
                    """
                    SELECT id
                    FROM characters
                    WHERE user_id = :uid
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"uid": int(getattr(user, "id", 0) or 0)},
            )
        ).mappings().first()
        if row and row.get("id") is not None:
            cid = int(row["id"])
    except Exception:
        cid = None

    return main_menu_kb(webapp_character_id=cid)


@router.message(CommandStart())
async def start(message: Message, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    await message.answer(
        _menu_text(message.from_user.first_name),
        reply_markup=await _menu_markup(db_session, int(message.from_user.id)),
    )


@router.callback_query(F.data == "menu:back")
async def menu_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    await safe_edit(
        call,
        _menu_text(call.from_user.first_name),
        reply_markup=await _menu_markup(db_session, int(call.from_user.id)),
    )
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
            res = await stash.character_stash_page(
                tg_id=call.from_user.id,
                character_id=cid,
                page=0,
                page_size=15,
            )
        except StashError as e:
            await call.answer(str(e) or "Не удалось открыть склад.", show_alert=True)
            return
        except Exception:
            await call.answer("Не удалось открыть склад.", show_alert=True)
            return

        await safe_edit(call, res.text, reply_markup=stash_kb(cid, res.page, res.total_pages))
        await call.answer()
        return

    await safe_edit(
        call,
        "<b>Склад</b>\nВыбери персонажа.",
        reply_markup=chars_pick_kb(chars, item_cb_prefix="char:stash", show_create=False),
    )
    await call.answer()


@router.callback_query(F.data == "menu:market")
async def menu_market(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    user = await StartService(db_session).ensure_user(call.from_user.id)

    balance = int(getattr(user, "balance", 0) or 0)

    svc = MarketService(db_session)
    base, mp = await svc.market_items_text(page=0, page_size=MARKET_PAGE_SIZE, exclude_user_id=int(user.id))

    hint = "\n\nПодсказка: напиши № товара 1–30, чтобы увидеть лоты.\nПример: <code>12</code>"
    text_out = base + hint

    balance_line = f"\n\nБаланс: {balance} монет"
    if len(text_out) + len(balance_line) > 3900:
        text_out = base + hint
        if len(text_out) + len(balance_line) > 3900:
            text_out = base

    text_out = text_out + balance_line

    page_item_ids = [int(x.item_id) for x in mp.items]

    out = await call.message.answer(
        text_out,
        reply_markup=market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
        disable_web_page_preview=True,
    )

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=out.chat.id,
        message_id=out.message_id,
        market_view="items",
        market_page=int(mp.page),
        market_page_item_ids=page_item_ids,
    )

    await safe_delete(call.message)

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
