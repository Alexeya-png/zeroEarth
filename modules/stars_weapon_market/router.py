from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.service import StartService
from modules.stars_weapon_market.keyboards import (
    stars_weapon_market_kb,
    stars_weapon_details_kb,
    cancel_to_market_kb,
    pick_character_kb,
    pick_buy_character_kb,
)
from modules.stars_weapon_market.service import StarsWeaponMarketService, StarsWeaponMarketError
from modules.stars_weapon_market.states import StarsWeaponMarketStates


router = Router()


PAGE_SIZE = 20


def _parse_int(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if not s.isdigit():
        return None
    try:
        return int(s)
    except Exception:
        return None


async def _render_market(call: CallbackQuery, db: AsyncSession, state: FSMContext, page: int) -> None:
    svc = StarsWeaponMarketService(db)
    text_out, mp = await svc.market_text(page=page, page_size=PAGE_SIZE)
    await state.set_state(StarsWeaponMarketStates.waiting_listing_id)
    await state.update_data(page=int(mp.page), listing_ids=[int(x.id) for x in mp.listings])
    await safe_edit(
        call,
        text_out,
        reply_markup=stars_weapon_market_kb(page=mp.page, has_prev=mp.has_prev, has_next=mp.has_next),
    )


@router.callback_query(F.data == "wstars:open")
async def wstars_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    await _render_market(call, db_session, state, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("wstars:page:"))
async def wstars_page(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    page = _parse_int(parts[2])
    if page is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    await _render_market(call, db_session, state, page=int(page))
    await call.answer()


@router.message(StarsWeaponMarketStates.waiting_listing_id)
async def wstars_pick_listing(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    listing_ids = list(data.get("listing_ids") or [])
    page = int(data.get("page") or 0)

    n = _parse_int(message.text)
    if n is None:
        await message.answer("Нужен номер лота.")
        return

    if n <= 0 or n > len(listing_ids):
        await message.answer("Нет такого номера на этой странице.")
        return

    listing_id = int(listing_ids[n - 1])

    svc = StarsWeaponMarketService(db_session)
    d = await svc.get_listing_details(listing_id)
    if not d or d.status != "active":
        await message.answer("Лот уже недоступен.")
        return

    can_buy = (d.seller_tg_id != int(message.from_user.id))
    text_out = svc.listing_details_text(d)

    await message.answer(
        text_out,
        reply_markup=stars_weapon_details_kb(
            page=page,
            listing_id=listing_id,
            can_buy=can_buy,
            price_stars=d.price_stars,
        ),
    )


@router.callback_query(F.data.startswith("wstars:details:"))
async def wstars_details(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    parts = (call.data or "").split(":")
    if len(parts) != 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    listing_id = _parse_int(parts[2])
    page = _parse_int(parts[3])
    if listing_id is None or page is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = StarsWeaponMarketService(db_session)
    d = await svc.get_listing_details(int(listing_id))
    if not d or d.status != "active":
        await call.answer("Лот уже недоступен.", show_alert=True)
        return

    can_buy = (d.seller_tg_id != int(call.from_user.id))
    await safe_edit(
        call,
        svc.listing_details_text(d),
        reply_markup=stars_weapon_details_kb(
            page=int(page),
            listing_id=int(listing_id),
            can_buy=can_buy,
            price_stars=d.price_stars,
        ),
    )
    await call.answer()


@router.callback_query(F.data == "wstars:sell")
async def wstars_sell_start(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    ss = StartService(db_session)
    chars = await ss.list_characters(call.from_user.id)
    if not chars:
        await call.answer("Нет персонажей.", show_alert=True)
        return

    await safe_edit(
        call,
        "<b>Выставить оружие</b>\nВыбери персонажа.",
        reply_markup=pick_character_kb(chars, item_cb_prefix="wstars:sellc"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("wstars:sellc:"))
async def wstars_sell_pick_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = _parse_int(parts[2])
    if character_id is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    svc = StarsWeaponMarketService(db_session)
    try:
        weapons = await svc.list_sellable_weapons(call.from_user.id, int(character_id), limit=60, offset=0)
    except StarsWeaponMarketError as e:
        await call.answer(str(e) or "Не удалось открыть оружие.", show_alert=True)
        return

    await state.set_state(StarsWeaponMarketStates.sell_choose_weapon)
    await state.update_data(character_id=int(character_id), weapon_ids=[int(w.weapon_id) for w in weapons])

    text_out = "<b>Выставить оружие</b>\nВыбери номер оружия и отправь в чат.\n\n" + svc.render_sellable_weapons_table(weapons)
    await safe_edit(call, text_out, reply_markup=cancel_to_market_kb())
    await call.answer()


@router.message(StarsWeaponMarketStates.sell_choose_weapon)
async def wstars_sell_choose_weapon(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    weapon_ids = list(data.get("weapon_ids") or [])
    character_id = int(data.get("character_id") or 0)

    n = _parse_int(message.text)
    if n is None:
        await message.answer("Нужен номер оружия.")
        return
    if n <= 0 or n > len(weapon_ids):
        await message.answer("Нет такого номера.")
        return

    weapon_id = int(weapon_ids[n - 1])
    await state.update_data(weapon_id=weapon_id)
    await state.set_state(StarsWeaponMarketStates.sell_choose_price)

    await message.answer("Введи цену в Stars (целое число).")


@router.message(StarsWeaponMarketStates.sell_choose_price)
async def wstars_sell_choose_price(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    character_id = int(data.get("character_id") or 0)
    weapon_id = int(data.get("weapon_id") or 0)

    price = _parse_int(message.text)
    if price is None or price <= 0:
        await message.answer("Цена должна быть целым числом больше 0.")
        return

    svc = StarsWeaponMarketService(db_session)
    try:
        await svc.create_listing(message.from_user.id, character_id, weapon_id, int(price))
    except StarsWeaponMarketError as e:
        await message.answer(str(e) or "Не удалось создать лот.")
        return

    await state.clear()
    await message.answer("Лот создан. Открой рынок: Рынок → ⭐ Оружие за Stars")


@router.callback_query(F.data == "wstars:withdraw")
async def wstars_withdraw_start(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    svc = StarsWeaponMarketService(db_session)
    listings = await svc.list_user_active_listings(call.from_user.id)

    await state.set_state(StarsWeaponMarketStates.withdraw_choose_listing)
    await state.update_data(withdraw_ids=[int(x.id) for x in listings])

    text_out = "<b>Снять с продажи</b>\nВыбери номер лота и отправь в чат.\n\n" + svc.render_user_listings_table(listings)
    await safe_edit(call, text_out, reply_markup=cancel_to_market_kb())
    await call.answer()


@router.message(StarsWeaponMarketStates.withdraw_choose_listing)
async def wstars_withdraw_choose_listing(message: Message, db_session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ids_ = list(data.get("withdraw_ids") or [])

    n = _parse_int(message.text)
    if n is None:
        await message.answer("Нужен номер лота.")
        return
    if n <= 0 or n > len(ids_):
        await message.answer("Нет такого номера.")
        return

    listing_id = int(ids_[n - 1])

    svc = StarsWeaponMarketService(db_session)
    try:
        await svc.withdraw_listing(message.from_user.id, listing_id)
    except StarsWeaponMarketError as e:
        await message.answer(str(e) or "Не удалось снять лот.")
        return

    await state.clear()
    await message.answer("Лот снят. Оружие возвращено на склад.")


@router.callback_query(F.data.startswith("wstars:buy:"))
async def wstars_buy(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    parts = (call.data or "").split(":")
    if len(parts) != 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    listing_id = _parse_int(parts[2])
    page = _parse_int(parts[3])
    if listing_id is None or page is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    ss = StartService(db_session)
    chars = await ss.list_characters(call.from_user.id)
    if not chars:
        await call.answer("Нет персонажей для получения.", show_alert=True)
        return

    if len(chars) == 1:
        cid = int(chars[0]["id"])
        await _send_invoice(call, db_session, int(listing_id), cid, int(page))
        return

    await safe_edit(
        call,
        "<b>Купить оружие</b>\nВыбери персонажа, которому добавить оружие на склад.",
        reply_markup=pick_buy_character_kb(chars, listing_id=int(listing_id), page=int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("wstars:buyc:"))
async def wstars_buy_choose_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    parts = (call.data or "").split(":")
    if len(parts) != 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    listing_id = _parse_int(parts[2])
    character_id = _parse_int(parts[3])
    page = _parse_int(parts[4])
    if listing_id is None or character_id is None or page is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    await _send_invoice(call, db_session, int(listing_id), int(character_id), int(page))


async def _send_invoice(call: CallbackQuery, db_session: AsyncSession, listing_id: int, character_id: int, page: int) -> None:
    svc = StarsWeaponMarketService(db_session)
    try:
        payload, amount, weapon_name = await svc.create_order_and_reserve(call.from_user.id, listing_id, character_id)
    except StarsWeaponMarketError as e:
        await call.answer(str(e) or "Не удалось создать платёж.", show_alert=True)
        return

    title = "Покупка оружия"
    description = f"{weapon_name} – лот #{listing_id}"

    prices = [LabeledPrice(label=weapon_name, amount=int(amount))]

    await call.bot.send_invoice(
        chat_id=call.from_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency=StarsWeaponMarketService.CURRENCY,
        prices=prices,
    )

    await safe_edit(call, "Счёт отправлен.", reply_markup=stars_weapon_details_kb(page=page, listing_id=listing_id, can_buy=False, price_stars=amount))
    await call.answer()


@router.pre_checkout_query()
async def wstars_pre_checkout(pre_checkout_query: PreCheckoutQuery, db_session: AsyncSession):
    svc = StarsWeaponMarketService(db_session)
    ok, err = await svc.validate_pre_checkout(
        tg_id=pre_checkout_query.from_user.id,
        payload=pre_checkout_query.invoice_payload,
        total_amount=pre_checkout_query.total_amount,
        currency=pre_checkout_query.currency,
    )
    if ok:
        await pre_checkout_query.answer(ok=True)
    else:
        await pre_checkout_query.answer(ok=False, error_message=err or "Платёж отклонён.")


@router.message(F.successful_payment)
async def wstars_successful_payment(message: Message, db_session: AsyncSession):
    sp = message.successful_payment
    if not sp:
        return

    payload = sp.invoice_payload or ""
    if not payload.startswith("wstars:"):
        return

    svc = StarsWeaponMarketService(db_session)
    try:
        text_out, _amount = await svc.finalize_payment(
            payload=payload,
            telegram_charge_id=sp.telegram_payment_charge_id,
            provider_charge_id=getattr(sp, "provider_payment_charge_id", None),
        )
    except StarsWeaponMarketError as e:
        await message.answer(str(e) or "Ошибка выдачи.")
        return

    await message.answer(text_out)
