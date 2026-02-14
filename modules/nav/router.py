# modules/nav/router.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from modules.start.keyboards import (
    main_menu_kb,
    chars_pick_kb,
    char_detail_kb,
    char_stash_kb,
)
from modules.start.service import StartService
from modules.stash.service import StashService, StashError

from modules.market.keyboards import market_kb
from modules.market.service import MarketService
from modules.market.states import MarketStates


router = Router()

MARKET_PAGE_SIZE = 30


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _menu_text(first_name: str | None) -> str:
    name = (first_name or "").strip()
    if name:
        return f"Привет, {_esc_html(name)}.\nВыбери раздел:"
    return "Привет.\nВыбери раздел:"


@router.message(Command("menu"))
@router.message(F.text == "Меню")
async def open_menu(message: Message, db_session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(message.from_user.id)
    await message.answer(_menu_text(message.from_user.first_name), reply_markup=main_menu_kb())


@router.message(Command("character"))
async def open_character(message: Message, db_session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(message.from_user.id)

    chars = await svc.list_characters(message.from_user.id)
    if not chars:
        await message.answer("У тебя нет персонажей.", reply_markup=chars_pick_kb([]))
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        try:
            text_out = await svc.character_details_text(tg_id=message.from_user.id, character_id=cid)
        except Exception:
            await message.answer("Персонаж не найден.")
            return
        await message.answer(text_out, reply_markup=char_detail_kb(cid))
        return

    await message.answer(
        "<b>Выбор персонажа</b>\nНажми на персонажа.",
        reply_markup=chars_pick_kb(chars),
    )


@router.message(Command("stash"))
async def open_stash(message: Message, db_session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    ss = StartService(db_session)
    await ss.ensure_user(message.from_user.id)

    chars = await ss.list_characters(message.from_user.id)
    if not chars:
        await message.answer("У тебя нет персонажей.", reply_markup=chars_pick_kb([]))
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        stash = StashService(db_session)
        try:
            text_out = await stash.character_stash_text(message.from_user.id, cid)
        except StashError as e:
            await message.answer(str(e) or "Не удалось открыть склад.")
            return
        except Exception:
            await message.answer("Не удалось открыть склад.")
            return
        await message.answer(text_out, reply_markup=char_stash_kb(cid))
        return

    await message.answer(
        "<b>Склад</b>\nВыбери персонажа.",
        reply_markup=chars_pick_kb(chars, item_cb_prefix="char:stash", show_create=False),
    )


@router.message(Command("market"))
async def open_market(message: Message, db_session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await StartService(db_session).ensure_user(message.from_user.id)

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

    out = await message.answer(
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


@router.message(Command("quests"))
async def open_quests(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("<b>Квесты</b>\nВ разработке.")
