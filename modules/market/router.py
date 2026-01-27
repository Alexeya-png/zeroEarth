from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from modules.common.tg import safe_edit
from modules.start.service import StartService

from .keyboards import market_kb
from .service import MarketService


router = Router()


@router.callback_query(F.data == "menu:market")
async def open_market(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()

    await StartService(db_session).ensure_user(call.from_user.id)

    svc = MarketService(db_session)
    text_out = await svc.market_text(limit=30)

    await safe_edit(call, text_out, reply_markup=market_kb())
    await call.answer()
