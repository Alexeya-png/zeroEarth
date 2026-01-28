from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.keyboards import chars_pick_kb
from modules.start.service import StartService

from .keyboards import market_kb, market_details_kb, market_sell_cancel_kb
from .service import MarketService, MarketError
from .states import MarketStates


router = Router()

PAGE_SIZE = 30


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
    await StartService(db_session).ensure_user(call.from_user.id)

    svc = MarketService(db_session)
    base, mp = await svc.market_text(page=int(page), page_size=PAGE_SIZE)

    hint = "\n\nПодсказка: напиши № строки 1–30, чтобы увидеть детали.\nПример: <code>12</code>"
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    if len(text_out) > 3900:
        text_out = base + hint

    page_listing_ids = [int(x.id) for x in mp.listings]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        market_page=int(mp.page),
        market_page_listing_ids=page_listing_ids,
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
    svc = MarketService(db_session)
    base, mp = await svc.market_text(page=int(page), page_size=PAGE_SIZE)

    hint = "\n\nПодсказка: напиши № строки 1–30, чтобы увидеть детали.\nПример: <code>12</code>"
    text_out = base + hint
    if notice:
        text_out = base + "\n" + notice + hint

    if len(text_out) > 3900:
        text_out = base + hint

    page_listing_ids = [int(x.id) for x in mp.listings]

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=chat_id,
        message_id=message_id,
        market_page=int(mp.page),
        market_page_listing_ids=page_listing_ids,
    )

    await _edit_message(
        message.bot,
        chat_id,
        message_id,
        text_out,
        market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )


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
    await _render_market(call, db_session, state, page=page)
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
        await _sell_show_inventory(call, db_session, state, character_id=cid)
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

    await _sell_show_inventory(call, db_session, state, character_id=cid)
    await call.answer()


@router.callback_query(F.data == "market:sell:cancel")
async def market_sell_cancel(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("market_page") or 0)
    await _render_market(call, db_session, state, page=page)
    await call.answer()


async def _sell_show_inventory(call: CallbackQuery, db_session: AsyncSession, state: FSMContext, *, character_id: int) -> None:
    svc = MarketService(db_session)
    text_out, items = await svc.sellable_inventory(call.from_user.id, character_id, limit=30, offset=0)

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
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    await safe_edit(call, text_out, reply_markup=market_sell_cancel_kb())


@router.message(MarketStates.waiting_listing_id)
async def market_details_from_chat(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    chat_id = int(data.get("chat_id") or message.chat.id)
    message_id = int(data.get("message_id") or 0)
    page = int(data.get("market_page") or 0)
    page_listing_ids = list(data.get("market_page_listing_ids") or [])

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен номер строки (№) из таблицы.")
        return

    n = int(raw)
    if n < 1 or n > len(page_listing_ids):
        await message.answer(f"Номер строки должен быть 1–{len(page_listing_ids)}.")
        return

    listing_id = int(page_listing_ids[n - 1])

    try:
        await message.delete()
    except Exception:
        pass

    svc = MarketService(db_session)
    text_out = await svc.listing_details_text(listing_id)

    await state.set_state(MarketStates.waiting_listing_id)
    await state.update_data(
        chat_id=chat_id,
        message_id=message_id,
        market_page=page,
        market_page_listing_ids=page_listing_ids,
    )

    if message_id > 0:
        try:
            await _edit_message(message.bot, chat_id, message_id, text_out, market_details_kb(page=page))
            return
        except Exception:
            pass

    await message.answer(text_out, reply_markup=market_details_kb(page=page), parse_mode="HTML")


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

    try:
        await message.delete()
    except Exception:
        pass

    if max_qty > 1:
        text_out = (
            "<b>Выставить на рынок</b>\n"
            f"Предмет: {_esc(name)}\n"
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
                pass
        await message.answer(text_out, reply_markup=market_sell_cancel_kb(), parse_mode="HTML")
        return

    text_out = (
        "<b>Выставить на рынок</b>\n"
        f"Предмет: {_esc(name)}\n"
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
            pass
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

    try:
        await message.delete()
    except Exception:
        pass

    text_out = (
        "<b>Выставить на рынок</b>\n"
        f"Предмет: {_esc(name)}\n"
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
            pass
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

    try:
        await message.delete()
    except Exception:
        pass

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

    notice = f"Выставлено: {_esc(name)} ×{qty} – {price} монет – лот #{listing_id}"

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
