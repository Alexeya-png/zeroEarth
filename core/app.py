from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import settings
from core.logging import setup_logging
from core.middlewares.db import DbSessionMiddleware
from db.session import create_engine_and_sessionmaker, init_db

from modules.characters.router import router as characters_router
from modules.range.router import router as range_router
from modules.stash.router import router as stash_router
from modules.start.router import router as start_router
from modules.weapon_upgrades.router import router as weapon_upgrades_router
from modules.fallback.router import router as fallback_router


async def run() -> None:
    setup_logging(settings.LOG_LEVEL)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    engine, sessionmaker = create_engine_and_sessionmaker(settings.DB_URL)
    await init_db(engine)

    dp.update.middleware(DbSessionMiddleware(sessionmaker))
    dp.include_router(characters_router)
    dp.include_router(stash_router)
    dp.include_router(range_router)
    dp.include_router(weapon_upgrades_router)
    dp.include_router(start_router)
    dp.include_router(fallback_router)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        await engine.dispose()
