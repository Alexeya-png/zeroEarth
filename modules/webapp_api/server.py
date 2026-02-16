from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Any
from urllib.parse import parse_qsl
import sys
from pathlib import Path

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def _load_env_file(path: Path) -> None:
    try:
        text_env = path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw in text_env.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        k, v = line.split("=", 1)
        k = k.strip()
        v = _strip_quotes(v.strip())

        if not k:
            continue

        if os.getenv(k) is None:
            os.environ[k] = v


def _load_dotenv() -> None:
    env_file = (os.getenv("ENV_FILE") or "").strip()
    if env_file:
        _load_env_file(Path(env_file))
        return

    candidates = [
        _PROJECT_ROOT / ".env",
        _PROJECT_ROOT / ".env.local",
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
    ]
    for p in candidates:
        if p.is_file():
            _load_env_file(p)


_load_dotenv()

from modules.market.service import MarketError, MarketService
from modules.start.service import StartService


def _json_default(o: Any):
    try:
        return float(o)
    except Exception:
        return str(o)


def _dsn_from_env() -> str | URL:
    dsn = (
        os.getenv("DB_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("DB_DSN")
        or os.getenv("POSTGRES_DSN")
        or os.getenv("PG_DSN")
        or ""
    ).strip()
    if not dsn:
        raise RuntimeError("No DB_URL/DATABASE_URL/DB_DSN/POSTGRES_DSN/PG_DSN env var")

    if dsn.startswith("postgres://"):
        dsn = "postgresql+asyncpg://" + dsn[len("postgres://") :]
    elif dsn.startswith("postgresql://"):
        dsn = "postgresql+asyncpg://" + dsn[len("postgresql://") :]

    rest = dsn.split("://", 1)[1] if "://" in dsn else dsn
    at_pos = rest.rfind("@")
    if at_pos == -1:
        return dsn

    creds = rest[:at_pos]
    after_at = rest[at_pos + 1 :]

    if "/" not in creds:
        return dsn
    if ":" not in creds:
        return dsn

    user, password = creds.split(":", 1)

    slash_pos = after_at.find("/")
    if slash_pos == -1:
        return dsn

    hostport = after_at[:slash_pos]
    path_q = after_at[slash_pos + 1 :]

    if ":" in hostport:
        host, port_s = hostport.split(":", 1)
        port = int(port_s) if port_s.isdigit() else None
    else:
        host, port = hostport, None

    if "?" in path_q:
        dbname, qs = path_q.split("?", 1)
    else:
        dbname, qs = path_q, ""

    query = dict(parse_qsl(qs, keep_blank_values=True))

    return URL.create(
        drivername="postgresql+asyncpg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=dbname,
        query=query,
    )


def _webapp_dir() -> str:
    p = (os.getenv("WEBAPP_DIR") or "webapp").strip()
    return os.path.abspath(p)


def _webapp_page() -> str:
    return (os.getenv("WEBAPP_PAGE") or "stash.html").strip()


class StashWebModule:
    _CAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[x×х]\s*(\d+(?:\.\d+)?)")

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        webapp_dir: str,
        page_name: str,
    ) -> None:
        self._sm = sessionmaker
        self._webapp_dir = webapp_dir
        self._page_name = page_name

    @staticmethod
    def _as_int(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None

    @staticmethod
    def _as_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    @staticmethod
    def _as_dict(v: Any) -> dict:
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.strip():
            try:
                j = json.loads(v)
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _as_list(v: Any) -> list:
        if isinstance(v, list):
            return v
        if isinstance(v, str) and v.strip():
            try:
                j = json.loads(v)
                return j if isinstance(j, list) else []
            except Exception:
                return []
        return []

    @staticmethod
    def _norm_caliber(s: str) -> str:
        x = str(s or "").strip()
        x = x.replace("×", "x").replace("х", "x").replace("Х", "x")
        x = x.lower()
        x = re.sub(r"\s+", "", x)
        x = re.sub(r"[^0-9a-zx]+", "", x)
        return x

    def _extract_caliber_code_from_name(self, name: str) -> str | None:
        s = str(name or "")
        s = s.replace("×", "x").replace("х", "x").replace("Х", "x")
        m = self._CAL_RE.search(s)
        if not m:
            return None
        a = m.group(1)
        b = m.group(2)
        return f"{a}x{b}"

    @staticmethod
    def _bot_token() -> str:
        return (
            os.getenv("BOT_TOKEN")
            or os.getenv("TG_BOT_TOKEN")
            or os.getenv("TELEGRAM_BOT_TOKEN")
            or ""
        ).strip()

    @classmethod
    def _validate_init_data(cls, init_data: str) -> dict[str, Any] | None:
        token = cls._bot_token()
        if not token:
            return None

        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got_hash = pairs.pop("hash", "")
        if not got_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc_hash, got_hash):
            return None

        user_raw = pairs.get("user") or "{}"
        try:
            user = json.loads(user_raw)
            if not isinstance(user, dict):
                user = {}
        except Exception:
            user = {}

        tg_id = 0
        try:
            tg_id = int(user.get("id") or 0)
        except Exception:
            tg_id = 0

        if tg_id <= 0:
            return None

        return {"tg_id": tg_id, "user": user, "data": pairs}

    @classmethod
    def _tg_id_from_request(cls, request: web.Request) -> int | None:
        init_data = (
            request.headers.get("X-Tg-InitData")
            or request.headers.get("X-Telegram-InitData")
            or request.query.get("initData")
            or request.query.get("init_data")
            or ""
        ).strip()

        if not init_data:
            return None

        v = cls._validate_init_data(init_data)
        if not v:
            return None

        tg_id = int(v.get("tg_id") or 0)
        return tg_id if tg_id > 0 else None

    async def _resolve_caliber_by_code(self, session: AsyncSession, code: str) -> dict[str, Any] | None:
        c = self._norm_caliber(code)
        if not c:
            return None

        row = (
            await session.execute(
                text(
                    """
                    SELECT id, code, name
                    FROM calibers
                    WHERE regexp_replace(
                            lower(replace(replace(replace(code,'×','x'),'х','x'),'Х','x')),
                            '[^0-9a-zx]',
                            '',
                            'g'
                          ) = :c
                       OR regexp_replace(
                            lower(replace(replace(replace(COALESCE(name,''),'×','x'),'х','x'),'Х','x')),
                            '[^0-9a-zx]',
                            '',
                            'g'
                          ) = :c
                    LIMIT 1
                    """
                ),
                {"c": c},
            )
        ).mappings().first()

        if not row:
            return None

        return {"id": int(row["id"]), "code": row.get("code"), "name": row.get("name")}

    def install(self, app: web.Application) -> None:
        app["sessionmaker"] = self._sm
        app["webapp_dir"] = self._webapp_dir
        app["webapp_page"] = self._page_name

        app.router.add_get("/", self.page)
        app.router.add_get("/stash", self.page)

        # market
        app.router.add_get("/market", self.market_page)

        app.router.add_get("/api/item", self.api_item)

        # market api
        app.router.add_get("/api/market/listings", self.api_market_listings)
        app.router.add_get("/api/market/lot", self.api_market_lot)
        app.router.add_post("/api/market/buy", self.api_market_buy)

        # user api
        app.router.add_get("/api/me/characters", self.api_me_characters)
        app.router.add_get("/api/me/stash", self.api_me_stash)

        app.router.add_static("/", self._webapp_dir, show_index=False)

    @staticmethod
    def _ammo_name_variants(item_name: str) -> list[str]:
        s = str(item_name or "").strip()
        if not s:
            return []

        def _norm(x: str) -> str:
            x = x.strip()
            x = x.replace("×", "x").replace("х", "x").replace("Х", "x")
            x = re.sub(r"\s+", " ", x)
            return x.strip()

        out: list[str] = []

        def _add(x: str):
            x = _norm(x)
            if x and x not in out:
                out.append(x)

        _add(s)

        low = s.lower().strip()
        for p in ("патроны", "патрон", "ammo", "cartridges"):
            if low.startswith(p):
                rest = s[len(p) :].strip(" -–—:;,.")
                _add(rest)

        tokens = _norm(s).split(" ")
        if tokens:
            last = tokens[-1].strip()
            if 1 <= len(last) <= 8 and re.fullmatch(r"[A-Za-z0-9\.\-]+", last):
                _add(last)

        return out[:10]

    @staticmethod
    def _norm_bullet_type(s: str) -> str:
        x = str(s or "").strip().upper()
        x = re.sub(r"\s+", "", x)
        return x

    @staticmethod
    def _extract_bullet_type(item_name: str, item_meta: dict) -> str | None:
        meta = item_meta or {}
        bt = meta.get("bullet_type") or meta.get("bulletType") or meta.get("type")
        if bt:
            bt2 = StashWebModule._norm_bullet_type(str(bt))
            return bt2 or None

        s = str(item_name or "").strip()
        if not s:
            return None

        s = s.replace("×", "x").replace("х", "x").replace("Х", "x")
        s = re.sub(r"\s+", " ", s).strip()
        tokens = s.split(" ")
        if not tokens:
            return None

        last = tokens[-1].strip()
        if 1 <= len(last) <= 12 and re.fullmatch(r"[A-Za-z0-9\.\-]+", last):
            last2 = StashWebModule._norm_bullet_type(last)
            return last2 or None

        return None

    async def _fetch_ammo_stats_by_bullet(
        self, session: "AsyncSession", caliber_id: int, bullet_type: str
    ) -> dict | None:
        bt = self._norm_bullet_type(bullet_type)
        if not bt:
            return None

        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, damage, armor_penetration
                    FROM ammo_types
                    WHERE caliber_id = :cid
                      AND upper(name) = upper(:bt)
                    LIMIT 1
                    """
                ),
                {"cid": caliber_id, "bt": bt},
            )
        ).mappings().first()

        if not row:
            return None

        return {
            "ammo_type_id": int(row["id"]),
            "name": str(row["name"]),
            "damage": self._as_int(row.get("damage")) or 0,
            "armor_penetration": self._as_int(row.get("armor_penetration")) or 0,
        }

    async def _fetch_market_stats(self, session: AsyncSession, item_id: int) -> dict[str, Any]:
        m = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt, AVG(price) AS avg_price
                    FROM market_listings
                    WHERE item_id = :id AND status = 'active'
                    """
                ),
                {"id": item_id},
            )
        ).mappings().first()

        s = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt, AVG(price_stars) AS avg_price
                    FROM stars_weapon_listings
                    WHERE weapon_id = :id AND status = 'active'
                    """
                ),
                {"id": item_id},
            )
        ).mappings().first()

        avg_price = self._as_float(m.get("avg_price") if m else None)
        stars_avg = self._as_float(s.get("avg_price") if s else None)

        return {
            "avg_price": int(round(avg_price)) if avg_price is not None else None,
            "count": self._as_int(m.get("cnt") if m else None) or 0,
            "stars_avg_price": int(round(stars_avg)) if stars_avg is not None else None,
            "stars_count": self._as_int(s.get("cnt") if s else None) or 0,
        }

    async def _fetch_ammo_stats(
        self,
        session: AsyncSession,
        *,
        caliber_id: int,
        item_name: str,
    ) -> dict[str, Any] | None:
        qs = self._ammo_name_variants(item_name)

        if qs:
            score_case = """
                CASE
                  WHEN lower(replace(replace(at.name,'×','x'),'х','x')) = lower(replace(replace(q.q,'×','x'),'х','x')) THEN 10000
                  WHEN position(lower(replace(replace(at.name,'×','x'),'х','x')) in lower(replace(replace(q.q,'×','x'),'х','x'))) > 0 THEN 1000 + length(at.name)
                  WHEN position(lower(replace(replace(q.q,'×','x'),'х','x')) in lower(replace(replace(at.name,'×','x'),'х','x'))) > 0 THEN 500 + length(q.q)
                  ELSE 0
                END
            """

            row = (
                await session.execute(
                    text(
                        f"""
                        WITH q AS (
                          SELECT unnest(:qs::text[]) AS q
                        )
                        SELECT
                          at.id,
                          at.name,
                          at.damage,
                          at.armor_penetration,
                          at.price,
                          MAX({score_case}) AS score
                        FROM ammo_types at
                        CROSS JOIN q
                        WHERE at.caliber_id = :cid
                        GROUP BY at.id, at.name, at.damage, at.armor_penetration, at.price
                        HAVING MAX({score_case}) > 0
                        ORDER BY score DESC, at.price DESC, at.id ASC
                        LIMIT 1
                        """
                    ),
                    {"cid": caliber_id, "qs": qs},
                )
            ).mappings().first()

            if row:
                return {
                    "ammo_type_id": int(row["id"]),
                    "name": str(row["name"]),
                    "damage": self._as_int(row.get("damage")) or 0,
                    "armor_penetration": self._as_int(row.get("armor_penetration")) or 0,
                }

        row2 = (
            await session.execute(
                text(
                    """
                    SELECT id, name, damage, armor_penetration, price
                    FROM ammo_types
                    WHERE caliber_id = :cid
                    ORDER BY price DESC, id ASC
                    LIMIT 1
                    """
                ),
                {"cid": caliber_id},
            )
        ).mappings().first()

        if not row2:
            return None

        return {
            "ammo_type_id": int(row2["id"]),
            "name": str(row2["name"]),
            "damage": self._as_int(row2.get("damage")) or 0,
            "armor_penetration": self._as_int(row2.get("armor_penetration")) or 0,
        }

    async def _fetch_weapon_unique(self, session: AsyncSession, weapon_id: int) -> dict[str, Any] | None:
        row = (
            await session.execute(
                text(
                    """
                    SELECT weapon_id, base_weapon_id, parent_weapon_id, mods_json, total_bonus_json,
                           is_locked, locked_listing_id
                    FROM weapon_uniques
                    WHERE weapon_id = :id
                    """
                ),
                {"id": weapon_id},
            )
        ).mappings().first()

        if not row:
            return None

        mods_json = row.get("mods_json") or []
        total_bonus = row.get("total_bonus_json") or {}

        mod_ids: list[int] = []
        if isinstance(mods_json, list):
            for m in mods_json:
                if isinstance(m, int):
                    mod_ids.append(m)
                    continue
                if isinstance(m, str) and m.isdigit():
                    mod_ids.append(int(m))
                    continue
                if isinstance(m, dict):
                    for k in ("item_id", "id", "mod_id"):
                        v = m.get(k)
                        if isinstance(v, int):
                            mod_ids.append(v)
                            break
                        if isinstance(v, str) and v.isdigit():
                            mod_ids.append(int(v))
                            break

        seen: set[int] = set()
        uniq_ids: list[int] = []
        for mid in mod_ids:
            if mid in seen:
                continue
            seen.add(mid)
            uniq_ids.append(mid)

        mods: list[dict[str, Any]] = []
        if uniq_ids:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT
                          i.id,
                          i.name,
                          wm.mod_type,
                          wm.tier,
                          wm.slot_limit,
                          wm.accuracy_bonus,
                          wm.reliability_bonus,
                          wm.damage_bonus,
                          wm.armor_pen_bonus,
                          wm.compatible_categories,
                          wm.unique_weapon_id,
                          wm.meta_json AS meta
                        FROM items i
                        JOIN weapon_mods wm ON wm.item_id = i.id
                        WHERE i.id = ANY(:ids::bigint[])
                        ORDER BY i.name
                        """
                    ),
                    {"ids": uniq_ids},
                )
            ).mappings().all()

            for r in rows:
                mods.append(
                    {
                        "id": int(r["id"]),
                        "name": str(r["name"]),
                        "mod_type": r.get("mod_type"),
                        "tier": r.get("tier"),
                        "slot_limit": self._as_int(r.get("slot_limit")) or 1,
                        "accuracy_bonus": self._as_int(r.get("accuracy_bonus")) or 0,
                        "reliability_bonus": self._as_int(r.get("reliability_bonus")) or 0,
                        "damage_bonus": self._as_int(r.get("damage_bonus")) or 0,
                        "armor_pen_bonus": self._as_int(r.get("armor_pen_bonus")) or 0,
                        "compatible_categories": r.get("compatible_categories") or [],
                        "unique_weapon_id": self._as_int(r.get("unique_weapon_id")),
                        "meta": r.get("meta") or {},
                    }
                )

        return {
            "weapon_id": int(row["weapon_id"]),
            "base_weapon_id": self._as_int(row.get("base_weapon_id")),
            "parent_weapon_id": self._as_int(row.get("parent_weapon_id")),
            "mods": mods,
            "total_bonus": total_bonus if isinstance(total_bonus, dict) else {},
            "is_locked": bool(row.get("is_locked") or False),
            "locked_listing_id": self._as_int(row.get("locked_listing_id")),
        }

    def _weapon_unique_from_meta(self, weapon_id: int, meta_any: Any) -> dict[str, Any] | None:
        meta = self._as_dict(meta_any)
        mods_src = meta.get("mods")
        if not isinstance(mods_src, list):
            return None

        mods: list[dict[str, Any]] = []
        total_bonus: dict[str, float] = {}

        def add_bonus(k: str, v: Any):
            try:
                n = float(v)
            except Exception:
                return
            if n == 0:
                return
            total_bonus[k] = float(total_bonus.get(k, 0.0)) + n

        for m in mods_src:
            mid = 0
            name = None
            acc = rel = dmg = ap = 0

            if isinstance(m, int):
                mid = m
            elif isinstance(m, str) and m.isdigit():
                mid = int(m)
            elif isinstance(m, dict):
                for k in ("item_id", "id", "mod_id"):
                    v = m.get(k)
                    if isinstance(v, int):
                        mid = v
                        break
                    if isinstance(v, str) and v.isdigit():
                        mid = int(v)
                        break

                name = m.get("name") or m.get("title")

                acc = self._as_int(m.get("accuracy_bonus") or m.get("acc_bonus") or 0) or 0
                rel = self._as_int(m.get("reliability_bonus") or m.get("rel_bonus") or 0) or 0
                dmg = self._as_int(m.get("damage_bonus") or m.get("dmg_bonus") or 0) or 0
                ap = (
                    self._as_int(
                        m.get("armor_pen_bonus")
                        or m.get("armor_penetration_bonus")
                        or m.get("ap_bonus")
                        or 0
                    )
                    or 0
                )

            if not name:
                name = f"Модуль {mid}" if mid else "Модуль"

            mods.append(
                {
                    "id": int(mid or 0),
                    "name": str(name),
                    "accuracy_bonus": int(acc),
                    "reliability_bonus": int(rel),
                    "damage_bonus": int(dmg),
                    "armor_pen_bonus": int(ap),
                }
            )

            add_bonus("accuracy_bonus", acc)
            add_bonus("reliability_bonus", rel)
            add_bonus("damage_bonus", dmg)
            add_bonus("armor_pen_bonus", ap)

        tb = meta.get("total_bonus") or meta.get("total_bonus_json") or meta.get("bonus_total") or {}
        if isinstance(tb, dict) and tb:
            total_bonus = {}
            for k, v in tb.items():
                try:
                    n = float(v)
                except Exception:
                    continue
                if n == 0:
                    continue
                total_bonus[str(k)] = n

        return {
            "weapon_id": int(weapon_id),
            "base_weapon_id": self._as_int(meta.get("base_weapon_id") or meta.get("baseWeaponId")),
            "parent_weapon_id": self._as_int(meta.get("parent_weapon_id") or meta.get("parentWeaponId")),
            "mods": mods,
            "total_bonus": total_bonus,
            "is_locked": bool(meta.get("is_locked") or meta.get("locked") or False),
            "locked_listing_id": self._as_int(meta.get("locked_listing_id") or meta.get("lockedListingId")),
        }

    async def _fetch_item(self, session: AsyncSession, item_id: int) -> dict[str, Any] | None:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                      i.id,
                      i.item_type,
                      i.name,
                      i.loot_type,
                      i.price,
                      COALESCE(i.weight_kg, i.weight, 0) AS weight_kg,
                      i.category,
                      i.accuracy,
                      i.reliability,
                      i.quality_score,
                      i.quality_tier,
                      i.caliber_id,
                      i.meta_json AS item_meta,

                      c.code AS caliber_code,
                      c.name AS caliber_name,

                      ies.tier AS eq_tier,
                      ies.armor,
                      ies.reliability AS eq_reliability,
                      ies.accuracy_bonus,
                      ies.reaction_bonus,
                      ies.initiative_bonus,
                      ies.stealth_bonus,
                      ies.carry_capacity_bonus,
                      ies.loot_analysis_bonus,
                      ies.item_handling_bonus,
                      ies.meta_json AS eq_meta,

                      wm.mod_type,
                      wm.tier AS mod_tier,
                      wm.slot_limit,
                      wm.accuracy_bonus AS mod_accuracy_bonus,
                      wm.reliability_bonus AS mod_reliability_bonus,
                      wm.damage_bonus,
                      wm.armor_pen_bonus,
                      wm.compatible_categories,
                      wm.unique_weapon_id,
                      wm.meta_json AS mod_meta
                    FROM items i
                    LEFT JOIN calibers c ON c.id = i.caliber_id
                    LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
                    LEFT JOIN weapon_mods wm ON wm.item_id = i.id
                    WHERE i.id = :id
                    """
                ),
                {"id": item_id},
            )
        ).mappings().first()

        if not row:
            return None

        out: dict[str, Any] = {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "item_type": str(row["item_type"]),
            "loot_type": str(row.get("loot_type") or "common"),
            "price": self._as_int(row.get("price")) or 0,
            "weight_kg": self._as_float(row.get("weight_kg")) or 0.0,
            "category": row.get("category"),
            "accuracy": self._as_int(row.get("accuracy")),
            "reliability": self._as_int(row.get("reliability")),
            "quality_score": self._as_int(row.get("quality_score")),
            "quality_tier": row.get("quality_tier"),
            "caliber": {
                "code": row.get("caliber_code"),
                "name": row.get("caliber_name"),
            }
            if row.get("caliber_code") or row.get("caliber_name")
            else None,
            "equipment_stats": None,
            "weapon_mod": None,
            "weapon_unique": None,
            "ammo_stats": None,
            "market": None,
            "meta": row.get("item_meta") or {},
        }

        if not isinstance(out["meta"], dict):
            out["meta"] = self._as_dict(out["meta"])

        if row.get("eq_tier") is not None:
            out["equipment_stats"] = {
                "tier": row.get("eq_tier"),
                "armor": self._as_int(row.get("armor")) or 0,
                "reliability": self._as_int(row.get("eq_reliability")) or 0,
                "accuracy_bonus": self._as_int(row.get("accuracy_bonus")) or 0,
                "reaction_bonus": self._as_float(row.get("reaction_bonus")) or 0.0,
                "initiative_bonus": self._as_float(row.get("initiative_bonus")) or 0.0,
                "stealth_bonus": self._as_float(row.get("stealth_bonus")) or 0.0,
                "carry_capacity_bonus": self._as_float(row.get("carry_capacity_bonus")) or 0.0,
                "loot_analysis_bonus": self._as_int(row.get("loot_analysis_bonus")) or 0,
                "item_handling_bonus": self._as_int(row.get("item_handling_bonus")) or 0,
                "meta": row.get("eq_meta") or {},
            }

        if row.get("mod_type") is not None:
            out["weapon_mod"] = {
                "mod_type": row.get("mod_type"),
                "tier": row.get("mod_tier"),
                "slot_limit": self._as_int(row.get("slot_limit")) or 1,
                "accuracy_bonus": self._as_int(row.get("mod_accuracy_bonus")) or 0,
                "reliability_bonus": self._as_int(row.get("mod_reliability_bonus")) or 0,
                "damage_bonus": self._as_int(row.get("damage_bonus")) or 0,
                "armor_pen_bonus": self._as_int(row.get("armor_pen_bonus")) or 0,
                "compatible_categories": row.get("compatible_categories") or [],
                "unique_weapon_id": self._as_int(row.get("unique_weapon_id")),
                "meta": row.get("mod_meta") or {},
            }

        out["market"] = await self._fetch_market_stats(session, item_id)

        item_type = str(out.get("item_type") or "").lower()

        if item_type == "ammo":
            caliber_id = self._as_int(row.get("caliber_id"))

            if not caliber_id:
                meta = out.get("meta") or {}
                code = meta.get("caliber_code") or meta.get("caliber") or meta.get("caliberCode")
                if code:
                    cal = await self._resolve_caliber_by_code(session, str(code))
                    if cal:
                        caliber_id = int(cal["id"])
                        out["caliber"] = {"code": cal.get("code"), "name": cal.get("name")}

            if not caliber_id:
                guessed = self._extract_caliber_code_from_name(str(out.get("name") or ""))
                if guessed:
                    cal = await self._resolve_caliber_by_code(session, guessed)
                    if cal:
                        caliber_id = int(cal["id"])
                        out["caliber"] = {"code": cal.get("code"), "name": cal.get("name")}

            if caliber_id:
                bt = self._extract_bullet_type(str(out.get("name") or ""), out.get("meta") or {})
                ammo = None
                if bt:
                    ammo = await self._fetch_ammo_stats_by_bullet(session, caliber_id=caliber_id, bullet_type=bt)

                if not ammo:
                    ammo = await self._fetch_ammo_stats(
                        session, caliber_id=caliber_id, item_name=str(out.get("name") or "")
                    )

                out["ammo_stats"] = ammo

        if item_type == "weapon":
            try:
                out["weapon_unique"] = await self._fetch_weapon_unique(session, item_id)
            except Exception:
                out["weapon_unique"] = None

            if out["weapon_unique"] is None:
                out["weapon_unique"] = self._weapon_unique_from_meta(item_id, out.get("meta"))

        return out

    async def api_item(self, request: web.Request) -> web.Response:
        item_id_raw = request.query.get("id") or ""
        if not item_id_raw.isdigit():
            return web.json_response({"ok": False, "error": "bad id"}, status=400)

        item_id = int(item_id_raw)

        async with self._sm() as s:
            data = await self._fetch_item(s, item_id)

        if not data:
            return web.json_response({"ok": False, "error": "not found"}, status=404)

        return web.json_response(
            {"ok": True, "item": data},
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def api_market_listings(self, request: web.Request) -> web.Response:
        item_id_raw = (request.query.get("item_id") or "").strip()
        item_id: int | None = None
        if item_id_raw:
            if not item_id_raw.isdigit():
                return web.json_response({"ok": False, "error": "bad item_id"}, status=400)
            item_id = int(item_id_raw)

        async with self._sm() as s:
            if item_id is not None:
                rows = (
                    await s.execute(
                        text(
                            """
                            SELECT ml.id, ml.qty, ml.price, ml.created_at
                            FROM market_listings ml
                            WHERE ml.item_id = :id AND ml.status = 'active'
                            ORDER BY ml.price ASC, ml.created_at ASC, ml.id ASC
                            LIMIT 200
                            """
                        ),
                        {"id": item_id},
                    )
                ).mappings().all()

                listings: list[dict[str, Any]] = []
                for r in rows or []:
                    listings.append(
                        {
                            "id": int(r.get("id") or 0),
                            "qty": self._as_int(r.get("qty")) or 0,
                            "price": self._as_int(r.get("price")) or 0,
                            "created_at": r.get("created_at"),
                        }
                    )

                return web.json_response(
                    {"ok": True, "item_id": int(item_id), "listings": listings},
                    dumps=lambda x: json.dumps(x, default=_json_default),
                )

            rows = (
                await s.execute(
                    text(
                        """
                        SELECT
                          ml.id,
                          ml.item_id,
                          ml.qty,
                          ml.price,
                          ml.created_at,
                          i.name AS item_name,
                          i.item_type AS item_type,
                          COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
                        FROM market_listings ml
                        JOIN items i ON i.id = ml.item_id
                        LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
                        LEFT JOIN weapon_mods wm ON wm.item_id = i.id
                        WHERE ml.status = 'active'
                        ORDER BY ml.created_at DESC, ml.id DESC
                        LIMIT 2000
                        """
                    )
                )
            ).mappings().all()

        listings: list[dict[str, Any]] = []
        for r in rows or []:
            listings.append(
                {
                    "id": int(r.get("id") or 0),
                    "item_id": int(r.get("item_id") or 0),
                    "item_name": str(r.get("item_name") or "Предмет"),
                    "item_type": str(r.get("item_type") or "misc"),
                    "tier": str(r.get("tier") or ""),
                    "qty": self._as_int(r.get("qty")) or 0,
                    "price": self._as_int(r.get("price")) or 0,
                    "created_at": r.get("created_at"),
                }
            )

        return web.json_response(
            {"ok": True, "listings": listings},
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def api_market_lot(self, request: web.Request) -> web.Response:
        lot_id_raw = request.query.get("id") or ""
        if not lot_id_raw.isdigit():
            return web.json_response({"ok": False, "error": "bad id"}, status=400)

        lot_id = int(lot_id_raw)

        async with self._sm() as s:
            r = (
                await s.execute(
                    text(
                        """
                        SELECT ml.id, ml.item_id, ml.qty, ml.price, ml.created_at,
                               ml.seller_user_id, u.tg_id AS seller_tg_id
                        FROM market_listings ml
                        JOIN users u ON u.id = ml.seller_user_id
                        WHERE ml.id = :id AND ml.status = 'active'
                        LIMIT 1
                        """
                    ),
                    {"id": lot_id},
                )
            ).mappings().first()

            if not r:
                return web.json_response({"ok": False, "error": "not found"}, status=404)

            item_id = int(r.get("item_id") or 0)
            item = await self._fetch_item(s, item_id) if item_id else None

        lot = {
            "id": int(r.get("id") or 0),
            "item_id": int(r.get("item_id") or 0),
            "qty": self._as_int(r.get("qty")) or 0,
            "price": self._as_int(r.get("price")) or 0,
            "created_at": r.get("created_at"),
            "seller_user_id": self._as_int(r.get("seller_user_id")) or 0,
            "seller_tg_id": self._as_int(r.get("seller_tg_id")) or 0,
        }

        return web.json_response(
            {"ok": True, "lot": lot, "item": item},
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def api_me_characters(self, request: web.Request) -> web.Response:
        tg_id = self._tg_id_from_request(request)
        if not tg_id:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        async with self._sm() as s:
            chars = await StartService(s).list_characters(int(tg_id))

        out: list[dict[str, Any]] = []
        for c in chars or []:
            out.append(
                {
                    "id": int(c.get("id") or 0),
                    "name": str(c.get("name") or ""),
                    "faction": str(c.get("faction") or ""),
                    "is_alive": bool(c.get("is_alive")) if c.get("is_alive") is not None else True,
                }
            )

        return web.json_response(
            {"ok": True, "characters": out},
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def api_me_stash(self, request: web.Request) -> web.Response:
        tg_id = self._tg_id_from_request(request)
        if not tg_id:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        page_raw = (request.query.get("page") or "0").strip()
        page_size_raw = (request.query.get("page_size") or request.query.get("limit") or "15").strip()

        character_id_raw = (
            request.query.get("character_id")
            or request.query.get("cid")
            or request.query.get("character")
            or ""
        ).strip()

        try:
            page = int(page_raw)
        except Exception:
            page = 0

        try:
            page_size = int(page_size_raw)
        except Exception:
            page_size = 15

        if page_size <= 0:
            page_size = 15
        if page_size > 50:
            page_size = 50

        character_id: int | None = None
        if character_id_raw.isdigit():
            character_id = int(character_id_raw)

        async with self._sm() as s:
            try:
                user = await StartService(s).ensure_user(int(tg_id))
            except Exception:
                return web.json_response({"ok": False, "error": "internal"}, status=500)

            if not character_id:
                row = (
                    await s.execute(
                        text(
                            """
                            SELECT id
                            FROM characters
                            WHERE user_id = :uid
                            ORDER BY created_at DESC, id DESC
                            LIMIT 1
                            """
                        ),
                        {"uid": int(user.id)},
                    )
                ).mappings().first()

                if row and row.get("id") is not None:
                    try:
                        character_id = int(row["id"])
                    except Exception:
                        character_id = None

            if not character_id:
                return web.json_response({"ok": False, "error": "no characters"}, status=404)

            ch = (
                await s.execute(
                    text(
                        """
                        SELECT id, name
                        FROM characters
                        WHERE id = :cid AND user_id = :uid
                        LIMIT 1
                        """
                    ),
                    {"cid": int(character_id), "uid": int(user.id)},
                )
            ).mappings().first()

            if not ch:
                return web.json_response({"ok": False, "error": "character not found"}, status=404)

            eq = (
                await s.execute(
                    text(
                        """
                        SELECT
                          e.head_item_id, e.body_item_id, e.gloves_item_id, e.boots_item_id,
                          e.weapon_1_id, e.weapon_2_id, e.weapon_3_id,

                          ih.name AS head_name,
                          COALESCE(ih.weight_kg, ih.weight, 0) AS head_weight,
                          COALESCE(ih.loot_type, 'common') AS head_loot_type,
                          COALESCE(ihs.tier, ih.quality_tier) AS head_tier,

                          ib.name AS body_name,
                          COALESCE(ib.weight_kg, ib.weight, 0) AS body_weight,
                          COALESCE(ib.loot_type, 'common') AS body_loot_type,
                          COALESCE(ibs.tier, ib.quality_tier) AS body_tier,

                          ig.name AS gloves_name,
                          COALESCE(ig.weight_kg, ig.weight, 0) AS gloves_weight,
                          COALESCE(ig.loot_type, 'common') AS gloves_loot_type,
                          COALESCE(igs.tier, ig.quality_tier) AS gloves_tier,

                          it.name AS boots_name,
                          COALESCE(it.weight_kg, it.weight, 0) AS boots_weight,
                          COALESCE(it.loot_type, 'common') AS boots_loot_type,
                          COALESCE(its.tier, it.quality_tier) AS boots_tier,

                          w1.name AS w1_name,
                          COALESCE(w1.weight_kg, w1.weight, 0) AS w1_weight,
                          COALESCE(w1.loot_type, 'common') AS w1_loot_type,
                          COALESCE(w1s.tier, w1.quality_tier) AS w1_tier,

                          w2.name AS w2_name,
                          COALESCE(w2.weight_kg, w2.weight, 0) AS w2_weight,
                          COALESCE(w2.loot_type, 'common') AS w2_loot_type,
                          COALESCE(w2s.tier, w2.quality_tier) AS w2_tier,

                          w3.name AS w3_name,
                          COALESCE(w3.weight_kg, w3.weight, 0) AS w3_weight,
                          COALESCE(w3.loot_type, 'common') AS w3_loot_type,
                          COALESCE(w3s.tier, w3.quality_tier) AS w3_tier
                        FROM equipment e
                        LEFT JOIN items ih ON ih.id = e.head_item_id
                        LEFT JOIN item_equipment_stats ihs ON ihs.item_id = e.head_item_id

                        LEFT JOIN items ib ON ib.id = e.body_item_id
                        LEFT JOIN item_equipment_stats ibs ON ibs.item_id = e.body_item_id

                        LEFT JOIN items ig ON ig.id = e.gloves_item_id
                        LEFT JOIN item_equipment_stats igs ON igs.item_id = e.gloves_item_id

                        LEFT JOIN items it ON it.id = e.boots_item_id
                        LEFT JOIN item_equipment_stats its ON its.item_id = e.boots_item_id

                        LEFT JOIN items w1 ON w1.id = e.weapon_1_id
                        LEFT JOIN item_equipment_stats w1s ON w1s.item_id = e.weapon_1_id

                        LEFT JOIN items w2 ON w2.id = e.weapon_2_id
                        LEFT JOIN item_equipment_stats w2s ON w2s.item_id = e.weapon_2_id

                        LEFT JOIN items w3 ON w3.id = e.weapon_3_id
                        LEFT JOIN item_equipment_stats w3s ON w3s.item_id = e.weapon_3_id
                        WHERE e.character_id = :cid
                        """
                    ),
                    {"cid": int(character_id)},
                )
            ).mappings().first()

            inv = (
                await s.execute(
                    text(
                        """
                        SELECT
                          ci.item_id,
                          i.name,
                          COALESCE(i.weight_kg, i.weight, 0) AS weight_each,
                          COALESCE(i.loot_type, 'common') AS loot_type,
                          COALESCE(ies.tier, wm.tier, i.quality_tier) AS tier,
                          ci.qty
                        FROM character_inventory ci
                        JOIN items i ON i.id = ci.item_id
                        LEFT JOIN item_equipment_stats ies ON ies.item_id = ci.item_id
                        LEFT JOIN weapon_mods wm ON wm.item_id = ci.item_id
                        WHERE ci.character_id = :cid
                        ORDER BY i.name
                        """
                    ),
                    {"cid": int(character_id)},
                )
            ).mappings().all()

            equipped_counts: dict[int, int] = {}
            if eq:
                for k in (
                    "head_item_id",
                    "body_item_id",
                    "gloves_item_id",
                    "boots_item_id",
                    "weapon_1_id",
                    "weapon_2_id",
                    "weapon_3_id",
                ):
                    v = eq.get(k)
                    if v:
                        try:
                            iid = int(v)
                        except Exception:
                            continue
                        equipped_counts[iid] = equipped_counts.get(iid, 0) + 1

            total_weight = 0.0
            if eq:
                for k in (
                    "head_weight",
                    "body_weight",
                    "gloves_weight",
                    "boots_weight",
                    "w1_weight",
                    "w2_weight",
                    "w3_weight",
                ):
                    total_weight += self._as_float(eq.get(k)) or 0.0

            armor: list[dict[str, Any]] = []
            if eq:
                for slot, id_k, n_k, w_k, lt_k, t_k in (
                    ("head", "head_item_id", "head_name", "head_weight", "head_loot_type", "head_tier"),
                    ("body", "body_item_id", "body_name", "body_weight", "body_loot_type", "body_tier"),
                    ("gloves", "gloves_item_id", "gloves_name", "gloves_weight", "gloves_loot_type", "gloves_tier"),
                    ("boots", "boots_item_id", "boots_name", "boots_weight", "boots_loot_type", "boots_tier"),
                ):
                    if not eq.get(id_k) or not eq.get(n_k):
                        continue
                    tier_v = str(eq.get(t_k) or "").strip()
                    armor.append(
                        {
                            "item_id": int(eq[id_k]),
                            "name": str(eq[n_k]),
                            "weight": self._as_float(eq.get(w_k)) or 0.0,
                            "tier": tier_v if tier_v else None,
                            "rarity": str(eq.get(lt_k) or "common"),
                            "slot": slot,
                        }
                    )

            weapons: list[dict[str, Any]] = []
            if eq:
                for id_k, n_k, w_k, lt_k, t_k in (
                    ("weapon_1_id", "w1_name", "w1_weight", "w1_loot_type", "w1_tier"),
                    ("weapon_2_id", "w2_name", "w2_weight", "w2_loot_type", "w2_tier"),
                    ("weapon_3_id", "w3_name", "w3_weight", "w3_loot_type", "w3_tier"),
                ):
                    if not eq.get(id_k) or not eq.get(n_k):
                        continue
                    tier_v = str(eq.get(t_k) or "").strip()
                    weapons.append(
                        {
                            "item_id": int(eq[id_k]),
                            "name": str(eq[n_k]),
                            "weight": self._as_float(eq.get(w_k)) or 0.0,
                            "tier": tier_v if tier_v else None,
                            "rarity": str(eq.get(lt_k) or "common"),
                            "slot": "weapon",
                        }
                    )

            inv_items_all: list[dict[str, Any]] = []
            if inv:
                for row in inv:
                    try:
                        item_id = int(row.get("item_id") or 0)
                    except Exception:
                        continue

                    if item_id <= 0:
                        continue

                    qty = self._as_int(row.get("qty")) or 1
                    eq_qty = equipped_counts.get(item_id, 0)
                    show_qty = qty - eq_qty
                    if show_qty <= 0:
                        continue

                    weight_each = self._as_float(row.get("weight_each")) or 0.0
                    w_total = float(show_qty) * float(weight_each)

                    total_weight += w_total

                    tier_v = str(row.get("tier") or "").strip()

                    inv_items_all.append(
                        {
                            "item_id": int(item_id),
                            "name": str(row.get("name") or ""),
                            "qty": int(show_qty),
                            "weight": float(w_total),
                            "tier": tier_v if tier_v else None,
                            "rarity": str(row.get("loot_type") or "common"),
                        }
                    )

            total_items = len(inv_items_all)
            total_pages = int((total_items + page_size - 1) // page_size) if total_items > 0 else 1

            if page < 0:
                page = 0
            if page >= total_pages:
                page = total_pages - 1

            start_i = page * page_size
            end_i = min(start_i + page_size, total_items)
            inv_page_items = inv_items_all[start_i:end_i]

            stash = {
                "character": {"id": int(ch["id"]), "name": str(ch.get("name") or "")},
                "total_weight": float(total_weight),
                "equipment": {"armor": armor, "weapons": weapons},
                "inventory": {
                    "page": int(page),
                    "total_pages": int(total_pages),
                    "total_items": int(total_items),
                    "page_size": int(page_size),
                    "items": inv_page_items,
                },
            }

        return web.json_response(
            {"ok": True, "stash": stash},
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def api_market_buy(self, request: web.Request) -> web.Response:
        tg_id = self._tg_id_from_request(request)
        if not tg_id:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        try:
            payload = await request.json()
        except Exception:
            payload = None

        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "bad json"}, status=400)

        lot_id = payload.get("lot_id")
        qty = payload.get("qty")
        character_id = payload.get("character_id")

        try:
            lot_id = int(lot_id)
            character_id = int(character_id)
            qty = int(qty) if qty is not None else None
        except Exception:
            return web.json_response({"ok": False, "error": "bad params"}, status=400)

        if lot_id <= 0 or character_id <= 0:
            return web.json_response({"ok": False, "error": "bad params"}, status=400)

        async with self._sm() as s:
            svc = MarketService(s)
            try:
                res = await svc.buy_listing_to_character(
                    tg_id=int(tg_id),
                    character_id=int(character_id),
                    listing_id=int(lot_id),
                    qty=qty,
                )
            except MarketError as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400)
            except Exception:
                return web.json_response({"ok": False, "error": "internal"}, status=500)

        return web.json_response(
            {
                "ok": True,
                "purchase": {
                    "listing_id": int(getattr(res, "listing_id", 0) or 0),
                    "item_id": int(getattr(res, "item_id", 0) or 0),
                    "item_name": str(getattr(res, "item_name", "") or ""),
                    "qty": int(getattr(res, "qty", 0) or 0),
                    "price": int(getattr(res, "price", 0) or 0),
                    "fee": int(getattr(res, "fee", 0) or 0),
                    "seller_user_id": int(getattr(res, "seller_user_id", 0) or 0),
                },
            },
            dumps=lambda x: json.dumps(x, default=_json_default),
        )

    async def page(self, request: web.Request) -> web.Response:
        page_name = request.app.get("webapp_page") or self._page_name
        return web.FileResponse(os.path.join(self._webapp_dir, page_name))

    async def market_page(self, request: web.Request) -> web.Response:
        p = os.path.join(self._webapp_dir, "market.html")
        if os.path.isfile(p):
            return web.FileResponse(p)
        return await self.page(request)


def create_app(sessionmaker: async_sessionmaker[AsyncSession]) -> web.Application:
    app = web.Application()

    webapp_dir = _webapp_dir()
    page_name = _webapp_page()

    StashWebModule(sessionmaker, webapp_dir=webapp_dir, page_name=page_name).install(app)
    return app


async def main(host: str, port: int):
    dsn = _dsn_from_env()
    engine = create_async_engine(dsn, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app(sm)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3001)
    args = ap.parse_args()

    asyncio.run(main(args.host, args.port))
