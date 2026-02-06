import asyncio
from contextlib import suppress

# core/app.py
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from core.config import settings
from core.logging import setup_logging
from core.middlewares.db import DbSessionMiddleware
from db.session import create_engine_and_sessionmaker, init_db

from modules.nav.router import router as nav_router
from modules.characters.router import router as characters_router
from modules.equip.router import router as equip_router
from modules.raids.router import router as raids_router
from modules.raids.engine import raids_ticker
from modules.range.router import router as range_router
from modules.stash.router import router as stash_router
from modules.market.router import router as market_router
from modules.stars_weapon_market.router import router as stars_weapon_market_router
from modules.start.router import router as start_router
from modules.weapon_upgrades.router import router as weapon_upgrades_router
from modules.fallback.router import router as fallback_router


async def run() -> None:
    setup_logging(settings.LOG_LEVEL)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть меню"),
            BotCommand(command="menu", description="Меню"),
            BotCommand(command="character", description="Персонаж"),
            BotCommand(command="stash", description="Склад"),
            BotCommand(command="market", description="Рынок"),
            BotCommand(command="quests", description="Квесты"),
        ]
    )

    dp = Dispatcher()

    engine, sessionmaker = create_engine_and_sessionmaker(settings.DB_URL)
    await init_db(engine)

    raids_task = asyncio.create_task(raids_ticker(sessionmaker, tick_seconds=60))

    dp.update.middleware(DbSessionMiddleware(sessionmaker))

    dp.include_router(nav_router)
    dp.include_router(start_router)
    dp.include_router(raids_router)
    dp.include_router(characters_router)
    dp.include_router(equip_router)
    dp.include_router(stash_router)
    dp.include_router(market_router)
    dp.include_router(stars_weapon_market_router)
    dp.include_router(range_router)
    dp.include_router(weapon_upgrades_router)
    dp.include_router(fallback_router)

    try:
        bal = await bot.get_my_star_balance()
        print(bal)
    except Exception:
        pass

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        raids_task.cancel()
        with suppress(asyncio.CancelledError):
            await raids_task
        await bot.session.close()
        await engine.dispose()
