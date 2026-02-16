# modules/market/router.py
from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit, safe_delete
from modules.start.keyboards import chars_pick_kb
from modules.start.service import StartService

from .keyboards import (
    market_kb,
    market_details_kb,
    market_buy_confirm_kb,
    market_buy_qty_kb,
    market_buy_qty_pick_char_kb,
    market_pick_char_kb,
    market_sell_cancel_kb,
    market_withdraw_cancel_kb,
)
from .service import MarketService, MarketError
from .states import MarketStates


LOG = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 30


def _withdraw_list_kb(*, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if has_prev:
        kb.button(text="←", callback_data=f"market:withdraw:page:{int(page - 1)}")
    if has_next:
        kb.button(text="→", callback_data=f"market:withdraw:page:{int(page + 1)}")

    kb.button(text="Отмена", callback_data="market:withdraw:cancel")
    kb.button(text="Меню", callback_data="menu:back")

    nav_cnt = int(bool(has_prev)) + int(bool(has_next))
    if nav_cnt:
        kb.adjust(nav_cnt, 1, 1)
    else:
        kb.adjust(1, 1)

    return kb.as_markup()


async def _edit_message(bot, chat_id: int, message_id: int, text_out: str, reply_markup) -> None:
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text_out,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _render_market(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, *, page: int, notice: str = "") -> None:
    user = await StartService(db_session).ensure_user(call.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    svc = MarketService(db_session)
    base, mp = await svc.market_items_text(page=int(page), page_size=PAGE_SIZE, exclude_user_id=int(user.id))

    hint = "\n\nПодсказка: нажми на название предмета, чтобы открыть лоты в веб-приложении."
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    balance_line = f"\n\nБаланс: {balance} монет"
    if len(text_out) + len(balance_line) > 3900:
        text_out = base + hint
        if len(text_out) + len(balance_line) > 3900:
            text_out = base

    text_out = text_out + balance_line

    page_item_ids = [int(x.item_id) for x in mp.items]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        market_view="items",
        market_page=int(mp.page),
        market_page_item_ids=page_item_ids,
    )

    await safe_edit(
        call,
        text_out,
        reply_markup=market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )


async def _render_market_from_message(
    message: Message,
    db_session: AsyncSession,
    state: FSMContext,
    *,
    chat_id: int,
    message_id: int,
    page: int,
    notice: str = "",
) -> None:
    user = await StartService(db_session).ensure_user(message.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    svc = MarketService(db_session)
    base, mp = await svc.market_items_text(page=int(page), page_size=PAGE_SIZE, exclude_user_id=int(user.id))

    hint = "\n\nПодсказка: нажми на название предмета, чтобы открыть лоты в веб-приложении."
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    balance_line = f"\n\nБаланс: {balance} монет"
    if len(text_out) + len(balance_line) > 3900:
        text_out = base + hint
        if len(text_out) + len(balance_line) > 3900:
            text_out = base

    text_out = text_out + balance_line

    page_item_ids = [int(x.item_id) for x in mp.items]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=chat_id,
        message_id=message_id,
        market_view="items",
        market_page=int(mp.page),
        market_page_item_ids=page_item_ids,
    )

    await _edit_message(
        message.bot,
        chat_id,
        message_id,
        text_out,
        market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )


async def _render_market_item_lots(
    call: CallbackQuery,
    db_session: AsyncSession,
    state: FSMContext,
    *,
    item_id: int,
    item_page: int,
    notice: str = "",
) -> None:
    user = await StartService(db_session).ensure_user(call.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    data = await state.get_data()
    market_page = int(data.get("market_page") or 0)

    svc = MarketService(db_session)
    base, mp = await svc.market_item_lots_text(int(item_id), page=int(item_page), page_size=PAGE_SIZE, exclude_user_id=int(user.id))

    hint = "\n\nПодсказка: напиши № лота 1–30, чтобы увидеть детали.\nПример: <code>12</code>\n0 – назад к товарам"
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    balance_line = f"\n\nБаланс: {balance} монет"
    if len(text_out) + len(balance_line) > 3900:
        text_out = base + hint
        if len(text_out) + len(balance_line) > 3900:
            text_out = base

    text_out = text_out + balance_line

    page_listing_ids = [int(x.id) for x in mp.listings]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        market_view="lots",
        market_page=int(market_page),
        market_item_id=int(mp.item_id),
        market_item_page=int(mp.page),
        market_item_page_listing_ids=page_listing_ids,
    )

    await safe_edit(
        call,
        text_out,
        reply_markup=market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )


async def _render_market_item_lots_from_message(
    message: Message,
    db_session: AsyncSession,
    state: FSMContext,
    *,
    chat_id: int,
    message_id: int,
    item_id: int,
    item_page: int,
    notice: str = "",
) -> None:
    user = await StartService(db_session).ensure_user(message.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    data = await state.get_data()
    market_page = int(data.get("market_page") or 0)

    svc = MarketService(db_session)
    base, mp = await svc.market_item_lots_text(int(item_id), page=int(item_page), page_size=PAGE_SIZE, exclude_user_id=int(user.id))

    hint = "\n\nПодсказка: напиши № лота 1–30, чтобы увидеть детали.\nПример: <code>12</code>\n0 – назад к товарам"
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    balance_line = f"\n\nБаланс: {balance} монет"
    if len(text_out) + len(balance_line) > 3900:
        text_out = base + hint
        if len(text_out) + len(balance_line) > 3900:
            text_out = base

    text_out = text_out + balance_line

    page_listing_ids = [int(x.id) for x in mp.listings]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=chat_id,
        message_id=message_id,
        market_view="lots",
        market_page=int(market_page),
        market_item_id=int(mp.item_id),
        market_item_page=int(mp.page),
        market_item_page_listing_ids=page_listing_ids,
    )

    await _edit_message(
        message.bot,
        chat_id,
        message_id,
        text_out,
        market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )

async def _delete_later(msg: Message, delay: float = 4.0) -> None:
    async def _task() -> None:
        await asyncio.sleep(delay)
        await safe_delete(msg)

    asyncio.create_task(_task())


async def _sell_total_count(svc: MarketService, character_id: int) -> int:
    r = (
        await svc.session.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM character_inventory ci
                JOIN items i ON i.id = ci.item_id
                WHERE ci.character_id = :cid
                  AND ci.qty > 0
                  AND NOT EXISTS (
                    SELECT 1
                    FROM equipment e
                    WHERE e.character_id = ci.character_id
                      AND ci.item_id IN (e.head_item_id, e.body_item_id, e.gloves_item_id, e.boots_item_id)
                  )
                """
            ),
            {"cid": int(character_id)},
        )
    ).mappings().first()
    return int((r or {}).get("cnt") or 0)


@router.callback_query(F.data == "menu:market")
async def open_market(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await _render_market(call, db_session, state, page=0)
    await call.answer()


@router.callback_query(F.data == "market:open")
async def market_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await _render_market(call, db_session, state, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("market:page:"))
async def market_page(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        page = int((call.data or "").split(":")[-1])
    except Exception:
        page = 0

    data = await state.get_data()
    view = str(data.get("market_view") or "items")

    if view in ("lots", "details"):
        item_id = int(data.get("market_item_id") or 0)
        if item_id > 0:
            await _render_market_item_lots(call, db_session, state, item_id=item_id, item_page=page)
            await call.answer()
            return

    await _render_market(call, db_session, state, page=page)
    await call.answer()


@router.callback_query(F.data.startswith("market:details:show:"))
async def market_details_show(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    svc = MarketService(db_session)
    l = await svc.get_listing(listing_id)
    if not l:
        await safe_edit(call, "<b>Рынок – подробнее</b>\n\nЛот не найден.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    text_out = await svc.listing_details_text(listing_id)
    is_owner = int(l.seller_tg_id or 0) == int(call.from_user.id)

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        market_view="details",
        market_page=int(page),
        market_item_id=int(getattr(l, "item_id", 0) or 0),
        market_item_page=0,
        market_item_page_listing_ids=[int(listing_id)],
    )

    await safe_edit(call, text_out, reply_markup=market_details_kb(page=page, listing_id=listing_id, is_owner=is_owner))
    await call.answer()


@router.message(MarketStates.waiting_listing_id)
async def market_details_from_chat(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    market_page = int(data.get("market_page") or 0)
    view = str(data.get("market_view") or "items")

    raw = (message.text or "").strip()

    if raw == "0":
        await safe_delete(message)

        if view == "details":
            item_id = int(data.get("market_item_id") or 0)
            item_page = int(data.get("market_item_page") or 0)
            if item_id > 0 and message_id > 0:
                await _render_market_item_lots_from_message(
                    message,
                    db_session,
                    state,
                    chat_id=chat_id,
                    message_id=message_id,
                    item_id=item_id,
                    item_page=item_page,
                )
                return

        if view in ("lots", "details"):
            if message_id > 0:
                await _render_market_from_message(
                    message,
                    db_session,
                    state,
                    chat_id=chat_id,
                    message_id=message_id,
                    page=market_page,
                )
                return

        return

    if not raw.isdigit():
        await safe_delete(message)
        m = await message.answer("Нужен номер строки из таблицы.")
        await _delete_later(m, 4.0)
        return

    n = int(raw)

    if view == "items":
        item_ids = list(data.get("market_page_item_ids") or [])
        if n < 1 or n > len(item_ids):
            await safe_delete(message)
            m = await message.answer("Нет такого номера.")
            await _delete_later(m, 4.0)
            return

        item_id = int(item_ids[n - 1])

        await safe_delete(message)

        if message_id > 0:
            await _render_market_item_lots_from_message(
                message,
                db_session,
                state,
                chat_id=chat_id,
                message_id=message_id,
                item_id=item_id,
                item_page=0,
            )
            return

        text_out, mp = await MarketService(db_session).market_item_lots_text(item_id, page=0, page_size=PAGE_SIZE)
        await message.answer(text_out, parse_mode="HTML", disable_web_page_preview=True)
        return

    listing_ids = list(data.get("market_item_page_listing_ids") or [])
    if n < 1 or n > len(listing_ids):
        await safe_delete(message)
        m = await message.answer("Нет такого номера.")
        await _delete_later(m, 4.0)
        return

    listing_id = int(listing_ids[n - 1])

    await safe_delete(message)

    svc = MarketService(db_session)
    l = await svc.get_listing(listing_id)
    if not l:
        m = await message.answer("Лот не найден.")
        await _delete_later(m, 4.0)
        return

    text_out = await svc.listing_details_text(listing_id)
    is_owner = int(l.seller_tg_id or 0) == int(message.from_user.id)

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=chat_id,
        message_id=message_id,
        market_view="details",
        market_page=market_page,
        market_item_id=int(getattr(l, "item_id", 0) or int(data.get("market_item_id") or 0)),
        market_item_page=int(data.get("market_item_page") or 0),
        market_item_page_listing_ids=listing_ids,
    )

    if message_id > 0:
        await _edit_message(
            message.bot,
            chat_id,
            message_id,
            text_out,
            market_details_kb(page=market_page, listing_id=listing_id, is_owner=is_owner),
        )
        return

    await message.answer(
        text_out,
        reply_markup=market_details_kb(page=market_page, listing_id=listing_id, is_owner=is_owner),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("market:details:buy_confirm:"))

async def market_details_buy_confirm(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    user = await StartService(db_session).ensure_user(call.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    svc = MarketService(db_session)
    l = await svc.get_listing(listing_id)
    if not l:
        await safe_edit(call, "Лот не найден.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    max_qty = max(1, int(getattr(l, "qty", 1) or 1))
    price_total = max(0, int(getattr(l, "price", 0) or 0))
    name = html_escape(str(getattr(l, "item_name", "Предмет") or "Предмет"))

    if max_qty <= 1:
        text_out = (
            "<b>Подтверждение покупки</b>\n"
            f"Лот #{listing_id}\n"
            f"Предмет: {name} ×1\n"
            f"Цена: {price_total} монет\n"
            f"Баланс: {balance} монет\n\n"
            "Подтвердить покупку?"
        )
        await safe_edit(call, text_out, reply_markup=market_buy_confirm_kb(listing_id=listing_id, page=page))
        await call.answer()
        return

    buy_qty = int(max_qty)
    buy_price = (price_total * buy_qty) // int(max_qty)

    await state.set_state(MarketStates.buy_choose_qty)
    await state.update_data(buy_listing_id=listing_id, buy_page=page, buy_qty=buy_qty)

    text_out = (
        "<b>Покупка</b>\n"
        f"Лот #{listing_id}\n"
        f"Предмет: {name}\n"
        f"Доступно: {max_qty}\n"
        f"Выбрано: {buy_qty}\n"
        f"Цена: {buy_price} монет\n"
        f"Баланс: {balance} монет\n\n"
        "Выбери количество и подтверди."
    )

    await safe_edit(call, text_out, reply_markup=market_buy_qty_kb(listing_id=listing_id, page=page, qty=buy_qty, max_qty=max_qty))
    await call.answer()


@router.callback_query(F.data == "market:noop")
async def market_noop(call: CallbackQuery):
    await call.answer()


async def _render_buy_qty(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, *, listing_id: int, page: int, qty: int | None = None) -> None:
    svc = MarketService(db_session)
    l = await svc.get_listing(int(listing_id))
    if not l:
        await safe_edit(call, "Лот не найден.", reply_markup=market_details_kb(page=int(page)))
        return

    user = await StartService(db_session).ensure_user(call.from_user.id)
    balance = int(getattr(user, "balance", 0) or 0)

    max_qty = max(1, int(getattr(l, "qty", 1) or 1))
    price_total = max(0, int(getattr(l, "price", 0) or 0))
    name = html_escape(str(getattr(l, "item_name", "Предмет") or "Предмет"))

    data = await state.get_data()
    cur_qty = int(data.get("buy_qty") or max_qty)
    if qty is not None:
        cur_qty = int(qty)

    if cur_qty < 1:
        cur_qty = 1
    if cur_qty > max_qty:
        cur_qty = max_qty

    buy_price = (price_total * cur_qty) // int(max_qty)

    await state.set_state(MarketStates.buy_choose_qty)
    await state.update_data(buy_listing_id=int(listing_id), buy_page=int(page), buy_qty=int(cur_qty))

    text_out = (
        "<b>Покупка</b>\n"
        f"Лот #{int(listing_id)}\n"
        f"Предмет: {name}\n"
        f"Доступно: {max_qty}\n"
        f"Выбрано: {cur_qty}\n"
        f"Цена: {buy_price} монет\n"
        f"Баланс: {balance} монет\n\n"
        "Выбери количество и подтверди."
    )

    await safe_edit(call, text_out, reply_markup=market_buy_qty_kb(listing_id=int(listing_id), page=int(page), qty=cur_qty, max_qty=max_qty))


@router.callback_query(F.data.startswith("market:buyqty:dec:"))
async def market_buyqty_dec(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    data = await state.get_data()
    cur_qty = int(data.get("buy_qty") or 1)
    await _render_buy_qty(call, db_session, state, listing_id=listing_id, page=page, qty=max(1, cur_qty - 1))
    await call.answer()


@router.callback_query(F.data.startswith("market:buyqty:inc:"))
async def market_buyqty_inc(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    svc = MarketService(db_session)
    l = await svc.get_listing(int(listing_id))
    if not l:
        await safe_edit(call, "Лот не найден.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    max_qty = max(1, int(getattr(l, "qty", 1) or 1))
    data = await state.get_data()
    cur_qty = int(data.get("buy_qty") or 1)
    await _render_buy_qty(call, db_session, state, listing_id=listing_id, page=page, qty=min(max_qty, cur_qty + 1))
    await call.answer()


@router.callback_query(F.data.startswith("market:buyqty:max:"))
async def market_buyqty_max(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    svc = MarketService(db_session)
    l = await svc.get_listing(int(listing_id))
    if not l:
        await safe_edit(call, "Лот не найден.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    max_qty = max(1, int(getattr(l, "qty", 1) or 1))
    await _render_buy_qty(call, db_session, state, listing_id=listing_id, page=page, qty=max_qty)
    await call.answer()


@router.callback_query(F.data.startswith("market:buyqty:confirm:"))
async def market_buyqty_confirm(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    data = await state.get_data()
    qty = int(data.get("buy_qty") or 1)

    ss = StartService(db_session)
    await ss.ensure_user(call.from_user.id)
    chars = await ss.list_characters(call.from_user.id)

    if not chars:
        await safe_edit(call, "Нет персонажей.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        svc = MarketService(db_session)
        try:
            res = await svc.buy_listing_to_character(call.from_user.id, cid, listing_id, qty=qty)
        except MarketError as e:
            await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
            await call.answer()
            return

        notice = f"Куплено: {html_escape(res.item_name)} ×{res.qty} – {res.price} монет – лот #{res.listing_id}"
        await _render_market(call, db_session, state, page=page, notice=notice)
        await call.answer()
        return

    text_out = "<b>Купить</b>\nВыбери персонажа, куда положить предмет."
    await safe_edit(call, text_out, reply_markup=market_buy_qty_pick_char_kb(chars, listing_id=listing_id, page=page))
    await call.answer()


@router.callback_query(F.data.startswith("market:buyqty:char:"))
async def market_buyqty_pick_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 6:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)
    character_id = int(parts[5] or 0)

    data = await state.get_data()
    qty = int(data.get("buy_qty") or 1)

    svc = MarketService(db_session)
    try:
        res = await svc.buy_listing_to_character(call.from_user.id, character_id, listing_id, qty=qty)
    except MarketError as e:
        await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    notice = f"Куплено: {html_escape(res.item_name)} ×{res.qty} – {res.price} монет – лот #{res.listing_id}"
    await _render_market(call, db_session, state, page=page, notice=notice)
    await call.answer()


@router.callback_query(F.data.startswith("market:details:buy:char:"))
async def market_details_buy_pick_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 7:
        await call.answer()
        return

    listing_id = int(parts[4] or 0)
    page = int(parts[5] or 0)
    character_id = int(parts[6] or 0)

    svc = MarketService(db_session)
    try:
        res = await svc.buy_listing_to_character(call.from_user.id, character_id, listing_id)
    except MarketError as e:
        await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    notice = f"Куплено: {html_escape(res.item_name)} ×{res.qty} – {res.price} монет – лот #{res.listing_id}"
    await _render_market(call, db_session, state, page=page, notice=notice)
    await call.answer()


@router.callback_query(F.data.startswith("market:details:buy:"))
async def market_details_buy(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    ss = StartService(db_session)
    await ss.ensure_user(call.from_user.id)
    chars = await ss.list_characters(call.from_user.id)

    if not chars:
        await safe_edit(call, "Нет персонажей.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        svc = MarketService(db_session)
        try:
            res = await svc.buy_listing_to_character(call.from_user.id, cid, listing_id)
        except MarketError as e:
            await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
            await call.answer()
            return

        notice = f"Куплено: {html_escape(res.item_name)} ×{res.qty} – {res.price} монет – лот #{res.listing_id}"
        await _render_market(call, db_session, state, page=page, notice=notice)
        await call.answer()
        return

    text_out = "<b>Купить</b>\nВыбери персонажа, куда положить предмет."
    await safe_edit(
        call,
        text_out,
        reply_markup=market_pick_char_kb(chars, action="buy", listing_id=listing_id, page=page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("market:details:withdraw:char:"))
async def market_details_withdraw_pick_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 7:
        await call.answer()
        return

    listing_id = int(parts[4] or 0)
    page = int(parts[5] or 0)
    character_id = int(parts[6] or 0)

    svc = MarketService(db_session)
    try:
        res = await svc.withdraw_listing_to_character(call.from_user.id, character_id, listing_id)
    except MarketError as e:
        await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    notice = f"Снято с продажи: {html_escape(res.item_name)} ×{res.qty} – лот #{res.listing_id}"
    await _render_market(call, db_session, state, page=page, notice=notice)
    await call.answer()


@router.callback_query(F.data.startswith("market:details:withdraw:"))
async def market_details_withdraw(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer()
        return

    listing_id = int(parts[3] or 0)
    page = int(parts[4] or 0)

    ss = StartService(db_session)
    await ss.ensure_user(call.from_user.id)
    chars = await ss.list_characters(call.from_user.id)

    if not chars:
        await safe_edit(call, "Нет персонажей.", reply_markup=market_details_kb(page=page))
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        svc = MarketService(db_session)
        try:
            res = await svc.withdraw_listing_to_character(call.from_user.id, cid, listing_id)
        except MarketError as e:
            await safe_edit(call, str(e), reply_markup=market_details_kb(page=page))
            await call.answer()
            return

        notice = f"Снято с продажи: {html_escape(res.item_name)} ×{res.qty} – лот #{res.listing_id}"
        await _render_market(call, db_session, state, page=page, notice=notice)
        await call.answer()
        return

    text_out = "<b>Снять с продажи</b>\nВыбери персонажа, куда вернуть предмет."
    await safe_edit(
        call,
        text_out,
        reply_markup=market_pick_char_kb(chars, action="withdraw", listing_id=listing_id, page=page),
    )
    await call.answer()


@router.callback_query(F.data == "market:sell")
async def market_sell(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    ss = StartService(db_session)
    user = await ss.ensure_user(call.from_user.id)
    chars = await ss.list_characters(call.from_user.id)

    data = await state.get_data()
    market_page = int(data.get("market_page") or 0)

    await state.update_data(
        market_page=market_page,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        sell_user_id=int(user.id),
    )

    if not chars:
        await safe_edit(call, "Нет персонажей.", reply_markup=market_sell_cancel_kb())
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        await _sell_show_inventory(call, db_session, state, character_id=cid, page=0)
        await call.answer()
        return

    text_out = "<b>Выставить на рынок</b>\nВыбери персонажа."
    kb = chars_pick_kb(
        chars,
        item_cb_prefix="market:sell:char",
        show_create=False,
        show_menu=True,
        menu_cb="market:sell:cancel",
        menu_text="Отмена",
    )
    await safe_edit(call, text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("market:sell:char:"))
async def market_sell_pick_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        cid = int((call.data or "").split(":")[-1])
    except Exception:
        cid = 0
    if cid <= 0:
        await call.answer()
        return

    await _sell_show_inventory(call, db_session, state, character_id=cid, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("market:sell:page:"))
async def market_sell_page(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        page = int((call.data or "").split(":")[-1])
    except Exception:
        page = 0

    data = await state.get_data()
    character_id = int(data.get("sell_character_id") or 0)
    if character_id <= 0:
        await call.answer()
        return

    await _sell_show_inventory(call, db_session, state, character_id=character_id, page=page)
    await call.answer()


@router.callback_query(F.data == "market:sell:cancel")
async def market_sell_cancel(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("market_page") or 0)
    await _render_market(call, db_session, state, page=page)
    await call.answer()


async def _sell_show_inventory(
    call: CallbackQuery,
    db_session: AsyncSession,
    state: FSMContext,
    *,
    character_id: int,
    page: int,
) -> None:
    svc = MarketService(db_session)

    total_cnt = await _sell_total_count(svc, int(character_id))
    page = max(0, int(page))

    if total_cnt <= 0:
        page = 0
        has_prev = False
        has_next = False
        offset = 0
    else:
        last_page = max(0, (total_cnt - 1) // PAGE_SIZE)
        if page > last_page:
            page = int(last_page)
        has_prev = page > 0
        has_next = page < last_page
        offset = int(page) * PAGE_SIZE

    text_out, items = await svc.sellable_inventory(call.from_user.id, character_id, limit=PAGE_SIZE, offset=offset)

    sell_items = [
        {
            "item_id": int(x.item_id),
            "qty": int(x.qty),
            "name": str(x.name),
            "item_type": str(x.item_type),
        }
        for x in items
    ]

    await state.set_state(MarketStates.sell_choose_item)
    await state.update_data(
        sell_character_id=int(character_id),
        sell_items=sell_items,
        sell_page=int(page),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    kb = market_sell_cancel_kb(page=int(page), has_prev=bool(has_prev), has_next=bool(has_next))
    await _edit_message(call.message.bot, call.message.chat.id, call.message.message_id, text_out, kb)


@router.callback_query(F.data == "market:withdraw")
async def market_withdraw(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    ss = StartService(db_session)
    await ss.ensure_user(call.from_user.id)
    chars = await ss.list_characters(call.from_user.id)

    data = await state.get_data()
    market_page = int(data.get("market_page") or 0)

    await state.update_data(
        market_page=market_page,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    if not chars:
        await safe_edit(call, "Нет персонажей.", reply_markup=market_withdraw_cancel_kb())
        await call.answer()
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        await _withdraw_show_listings(call, db_session, state, character_id=cid, page=0)
        await call.answer()
        return

    text_out = "<b>Снять с продажи</b>\nВыбери персонажа."
    kb = chars_pick_kb(
        chars,
        item_cb_prefix="market:withdraw:char",
        show_create=False,
        show_menu=True,
        menu_cb="market:withdraw:cancel",
        menu_text="Отмена",
    )
    await safe_edit(call, text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("market:withdraw:char:"))
async def market_withdraw_pick_char(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        cid = int((call.data or "").split(":")[-1])
    except Exception:
        cid = 0
    if cid <= 0:
        await call.answer()
        return

    await _withdraw_show_listings(call, db_session, state, character_id=cid, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("market:withdraw:page:"))
async def market_withdraw_page(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    try:
        page = int((call.data or "").split(":")[-1])
    except Exception:
        page = 0

    data = await state.get_data()
    character_id = int(data.get("withdraw_character_id") or 0)
    if character_id <= 0:
        await call.answer()
        return

    await _withdraw_show_listings(call, db_session, state, character_id=character_id, page=page)
    await call.answer()


@router.callback_query(F.data == "market:withdraw:cancel")
async def market_withdraw_cancel(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("market_page") or 0)
    await _render_market(call, db_session, state, page=page)
    await call.answer()


async def _withdraw_show_listings(
    call: CallbackQuery,
    db_session: AsyncSession,
    state: FSMContext,
    *,
    character_id: int,
    page: int,
) -> None:
    svc = MarketService(db_session)
    page = max(0, int(page))

    uid = await svc.ensure_user_id(call.from_user.id)
    total_row = (
        await svc.session.execute(
            text(
                """
                SELECT count(*)
                FROM market_listings ml
                WHERE ml.seller_user_id = :uid AND ml.status = 'active'
                """
            ),
            {"uid": int(uid)},
        )
    ).first()
    total_cnt = int((total_row[0] if total_row else 0) or 0)

    if total_cnt <= 0:
        page = 0
    else:
        last_page = max(0, (total_cnt - 1) // PAGE_SIZE)
        if page > last_page:
            page = int(last_page)

    offset = int(page) * PAGE_SIZE
    text_out, listings = await svc.withdrawable_listings_text(
        call.from_user.id,
        character_id,
        limit=PAGE_SIZE,
        offset=offset,
    )

    has_prev = page > 0
    has_next = total_cnt > (page + 1) * PAGE_SIZE

    listing_ids = [int(x.id) for x in listings]

    await state.set_state(MarketStates.withdraw_choose_listing)
    await state.update_data(
        withdraw_character_id=int(character_id),
        withdraw_listing_ids=listing_ids,
        withdraw_page=int(page),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    await safe_edit(call, text_out, reply_markup=_withdraw_list_kb(page=page, has_prev=has_prev, has_next=has_next))


@router.message(MarketStates.withdraw_choose_listing)
async def market_withdraw_choose_listing(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    character_id = int(data.get("withdraw_character_id") or 0)
    listing_ids = list(data.get("withdraw_listing_ids") or [])
    market_page = int(data.get("market_page") or 0)

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен номер строки (№) из таблицы.")
        return

    n = int(raw)
    if n < 1 or n > len(listing_ids):
        await message.answer(f"Номер строки должен быть 1–{len(listing_ids)}.")
        return

    listing_id = int(listing_ids[n - 1])

    await safe_delete(message)

    svc = MarketService(db_session)
    try:
        res = await svc.withdraw_listing_to_character(
            tg_id=message.from_user.id,
            character_id=character_id,
            listing_id=listing_id,
        )
    except MarketError as e:
        await message.answer(str(e))
        return

    notice = f"Снято с продажи: {html_escape(res.item_name)} ×{res.qty} – лот #{res.listing_id}"

    if message_id > 0:
        await _render_market_from_message(
            message,
            db_session,
            state,
            chat_id=chat_id,
            message_id=message_id,
            page=market_page,
            notice=notice,
        )
        return

    await message.answer(notice, parse_mode="HTML")


@router.message(MarketStates.sell_choose_item)
async def market_sell_choose_item(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    character_id = int(data.get("sell_character_id") or 0)
    sell_items = list(data.get("sell_items") or [])
    market_page = int(data.get("market_page") or 0)

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен номер строки (№) из таблицы.")
        return

    n = int(raw)
    if n < 1 or n > len(sell_items):
        await message.answer(f"Номер строки должен быть 1–{len(sell_items)}.")
        return

    item = sell_items[n - 1]
    item_id = int(item.get("item_id") or 0)
    max_qty = int(item.get("qty") or 1)
    name = str(item.get("name") or "Предмет")

    await safe_delete(message)

    if max_qty > 1:
        text_out = (
            "<b>Выставить на рынок</b>\n"
            f"Предмет: {html_escape(name)}\n"
            f"Доступно: {max_qty}\n"
            "Напиши количество."
        )
        await state.set_state(MarketStates.sell_choose_qty)
        await state.update_data(
            sell_character_id=character_id,
            sell_item_id=item_id,
            sell_item_name=name,
            sell_max_qty=max_qty,
            market_page=market_page,
            chat_id=chat_id,
            message_id=message_id,
        )
        if message_id > 0:
            try:
                await _edit_message(message.bot, chat_id, message_id, text_out, market_sell_cancel_kb())
                return
            except Exception:
                LOG.debug("edit_message failed", exc_info=True)
        await message.answer(text_out, reply_markup=market_sell_cancel_kb(), parse_mode="HTML")
        return

    text_out = (
        "<b>Выставить на рынок</b>\n"
        f"Предмет: {html_escape(name)}\n"
        "Напиши цену."
    )
    await state.set_state(MarketStates.sell_choose_price)
    await state.update_data(
        sell_character_id=character_id,
        sell_item_id=item_id,
        sell_item_name=name,
        sell_qty=1,
        market_page=market_page,
        chat_id=chat_id,
        message_id=message_id,
    )

    if message_id > 0:
        try:
            await _edit_message(message.bot, chat_id, message_id, text_out, market_sell_cancel_kb())
            return
        except Exception:
            LOG.debug("edit_message failed", exc_info=True)
    await message.answer(text_out, reply_markup=market_sell_cancel_kb(), parse_mode="HTML")


@router.message(MarketStates.sell_choose_qty)
async def market_sell_choose_qty(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    max_qty = int(data.get("sell_max_qty") or 1)
    name = str(data.get("sell_item_name") or "Предмет")

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно число.")
        return

    qty = int(raw)
    if qty < 1 or qty > max_qty:
        await message.answer(f"Количество должно быть 1–{max_qty}.")
        return

    await safe_delete(message)

    text_out = (
        "<b>Выставить на рынок</b>\n"
        f"Предмет: {html_escape(name)}\n"
        f"Количество: {qty}\n"
        "Напиши цену."
    )

    await state.set_state(MarketStates.sell_choose_price)
    await state.update_data(sell_qty=qty)

    if message_id > 0:
        try:
            await _edit_message(message.bot, chat_id, message_id, text_out, market_sell_cancel_kb())
            return
        except Exception:
            LOG.debug("edit_message failed", exc_info=True)
    await message.answer(text_out, reply_markup=market_sell_cancel_kb(), parse_mode="HTML")


@router.message(MarketStates.sell_choose_price)
async def market_sell_choose_price(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    character_id = int(data.get("sell_character_id") or 0)
    item_id = int(data.get("sell_item_id") or 0)
    qty = int(data.get("sell_qty") or 1)
    name = str(data.get("sell_item_name") or "Предмет")
    market_page = int(data.get("market_page") or 0)

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно число.")
        return

    price = int(raw)
    if price < 0:
        await message.answer("Цена должна быть 0 или больше.")
        return

    await safe_delete(message)

    svc = MarketService(db_session)
    try:
        listing_id = await svc.create_listing_from_character(
            tg_id=message.from_user.id,
            character_id=character_id,
            item_id=item_id,
            qty=qty,
            price=price,
        )
    except MarketError as e:
        await message.answer(str(e))
        return

    notice = f"Выставлено: {html_escape(name)} ×{qty} – {price} монет – лот #{listing_id}"

    if message_id > 0:
        await _render_market_from_message(
            message,
            db_session,
            state,
            chat_id=chat_id,
            message_id=message_id,
            page=market_page,
            notice=notice,
        )
        return

    await message.answer(notice, parse_mode="HTML")
