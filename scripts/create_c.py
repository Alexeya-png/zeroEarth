import argparse
import asyncio
import os
import random
from typing import Dict, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

load_dotenv()


def _normalize_db_url(url: str) -> str:
    # postgres:// -> postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    # add asyncpg driver
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

    # parse query, remove statement_cache_size if present
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q.pop("statement_cache_size", None)

    # Supabase требует SSL (asyncpg понимает ssl=require как ssl=True через диалект)
    if "ssl" not in q:
        q["ssl"] = "require"

    new_query = urlencode(q)
    return urlunparse(p._replace(query=new_query))


def roll_base_stats() -> Dict[str, int]:
    stats = {"endurance": 2, "agility": 2, "intelligence": 2}
    points = 3
    keys = list(stats.keys())
    while points > 0:
        candidates = [k for k in keys if stats[k] < 4]
        stats[random.choice(candidates)] += 1
        points -= 1
    return stats


def calc_derived(endurance: int, agility: int, intelligence: int) -> Dict[str, float | int]:
    return {
        "hp": 100 + (endurance * 2),
        "carry_capacity": float(70 + (endurance * 2.5)),
        "load": 0.0,

        "reaction": float(2 + (agility * 0.5)),
        "accuracy": 15 + agility,
        "initiative": float(agility * 0.5),
        "stealth": float(1 + (agility * 0.5)),

        "tech_training": intelligence,
        "hacking": intelligence,
        "loot_analysis": 1 + intelligence,

        "loot_modding": intelligence,
        "repair": intelligence,
        "chem_modding": intelligence,
    }


async def ensure_user(session, tg_id: int) -> int:
    row = (
        await session.execute(
            text("SELECT id FROM users WHERE tg_id = :tg_id"),
            {"tg_id": tg_id},
        )
    ).first()

    if row:
        return int(row[0])

    row = (
        await session.execute(
            text("INSERT INTO users (tg_id) VALUES (:tg_id) RETURNING id"),
            {"tg_id": tg_id},
        )
    ).first()
    return int(row[0])


async def create_character(
    db_url: str, tg_id: int, name: str | None, faction: str
) -> Tuple[int, Dict[str, int], Dict[str, float | int]]:
    engine = create_async_engine(
        _normalize_db_url(db_url),
        future=True,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},  # важно для PgBouncer/pooler
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)

    stats = roll_base_stats()
    derived = calc_derived(stats["endurance"], stats["agility"], stats["intelligence"])

    async with Session() as session:
        user_id = await ensure_user(session, tg_id)

        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO characters (
                      user_id, name, faction, is_alive,
                      endurance, agility, intelligence,
                      hp, carry_capacity, load,
                      reaction, accuracy, initiative, stealth,
                      tech_training, hacking, loot_analysis, loot_modding, repair, chem_modding
                    )
                    VALUES (
                      :user_id, :name, :faction, TRUE,
                      :endurance, :agility, :intelligence,
                      :hp, :carry_capacity, :load,
                      :reaction, :accuracy, :initiative, :stealth,
                      :tech_training, :hacking, :loot_analysis, :loot_modding, :repair, :chem_modding
                    )
                    RETURNING id
                    """
                ),
                {"user_id": user_id, "name": name, "faction": faction, **stats, **derived},
            )
        ).first()

        cid = int(row[0])

        await session.execute(text("INSERT INTO equipment (character_id) VALUES (:cid)"), {"cid": cid})
        await session.execute(text("INSERT INTO character_faction_profile (character_id) VALUES (:cid)"), {"cid": cid})

        await session.commit()

    await engine.dispose()
    return cid, stats, derived


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tg-id", type=int, required=True)
    p.add_argument("--name", type=str, default=None)
    p.add_argument("--faction", type=str, default="civilians", choices=["civilians", "containment", "servoclass"])
    return p.parse_args()


async def _amain():
    args = parse_args()
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Set DB_URL (or DATABASE_URL) in environment or .env")

    cid, stats, derived = await create_character(db_url, args.tg_id, args.name, args.faction)
    print(f"character_id={cid}")
    print(f"base_stats={stats}")
    print(f"derived={derived}")


if __name__ == "__main__":
    asyncio.run(_amain())
