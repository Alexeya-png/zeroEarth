from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine
from sqlalchemy import text
from db.base import Base


def create_engine_and_sessionmaker(db_url: str):
    if db_url.startswith("sqlite+aiosqlite:///./"):
        Path("./data").mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(db_url, pool_pre_ping=True, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessionmaker


async def init_db(engine: AsyncEngine) -> None:
    backend = engine.url.get_backend_name()

    async with engine.begin() as conn:
        if backend == "sqlite":
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.run_sync(Base.metadata.create_all)
        else:
            return
