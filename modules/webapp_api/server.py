from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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
        self, session: 'AsyncSession', caliber_id: int, bullet_type: str
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
                ap = self._as_int(m.get("armor_pen_bonus") or m.get("armor_penetration_bonus") or m.get("ap_bonus") or 0) or 0

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

        # normalize meta to dict
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
