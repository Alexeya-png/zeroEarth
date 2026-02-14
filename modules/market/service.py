# modules/market/service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping, Optional
from collections.abc import Mapping as AbcMapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MarketError(Exception):
    pass


MARKET_FEE_PCT = 0  # комиссия рынка в процентах, сейчас 0



def _miniapp_href(start_param: str) -> str:
    bot_username = (os.getenv("TG_BOT_USERNAME") or "").lstrip("@")
    webapp_name = (os.getenv("TG_WEBAPP_NAME") or "").strip().lstrip("/")

    if not bot_username:
        bot_username = "zeroearth_bot"
    if not webapp_name:
        webapp_name = "stash"

    return f"https://t.me/{bot_username}/{webapp_name}?startapp={start_param}"


def _link_name(item_id: Any, escaped_name: str) -> str:
    if item_id is None:
        return escaped_name
    try:
        iid = int(item_id)
    except Exception:
        return escaped_name
    if iid <= 0:
        return escaped_name

    href = _miniapp_href(f"i{iid}")
    return f'<a href="{href}">{escaped_name}</a>'


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_dt(v: Any) -> str:
    if isinstance(v, datetime):
        try:
            return v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return v.strftime("%Y-%m-%d %H:%M")
    s = str(v or "")
    if "T" in s:
        s = s.replace("T", " ")
    return s[:16]


def _fmt_date(v: Any) -> str:
    s = _fmt_dt(v)
    return s[:10]


def _clean_cell(v: Any) -> str:
    txt = str(v or "")
    return txt.replace("\n", " ").replace("\r", " ")


def _truncate(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    return s[:width]


def _cell(v: Any, width: int, *, align: str = "left") -> str:
    raw = _truncate(_clean_cell(v), width)
    if align == "right":
        raw = raw.rjust(width)
    else:
        raw = raw.ljust(width)
    return _esc(raw)


def _signed(v: Any, *, decimals: int = 1) -> str:
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0

    if abs(n) < 1e-12:
        return "0"

    if abs(n - int(n)) < 1e-12:
        return f"{int(n):+d}"

    s = f"{n:+.{int(decimals)}f}"
    return s.rstrip("0").rstrip(".")


def _is_zero_value(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, (int, float)):
        return abs(float(v)) < 1e-12
    s = str(v).strip()
    if not s:
        return True
    try:
        return abs(float(s.replace(",", "."))) < 1e-12
    except Exception:
        return False


def _kv_text(rows: list[tuple[str, Any]]) -> str:
    lines: list[str] = []
    for k, v in rows:
        kk = _esc(str(k))
        vv = "–" if v is None else str(v).strip()
        if not vv:
            vv = "–"
        lines.append(f"{kk}: <b>{_esc(vv)}</b>")
    return "\n".join(lines).rstrip()


def _mask_tg(tg_id: int | str | None) -> str:
    if tg_id is None:
        return "–"
    s = str(tg_id).strip()
    if not s:
        return "–"
    if len(s) <= 8:
        return s
    return s[:4] + "****" + s[-4:]


def _price_line(price: int) -> str:
    p = max(0, int(price))
    if p == 0:
        return "Цена: бесплатно"
    return f"Цена: <b>{p}</b>"


def _bullet_list(names: list[str], *, limit: int = 25) -> str:
    cleaned = [str(x).strip() for x in (names or []) if str(x).strip()]
    if not cleaned:
        return "–"
    out = []
    for n in cleaned[: max(1, int(limit))]:
        out.append(f"-{_esc(n)}")
    return "\n".join(out).rstrip()


@dataclass(frozen=True)
class PurchaseResult:
    listing_id: int
    item_id: int
    item_name: str
    qty: int
    price: int
    fee: int
    seller_user_id: int


@dataclass(frozen=True)
class MarketListingView:
    id: int
    item_id: int
    item_name: str
    item_type: str
    qty: int
    price: int
    tier: str


@dataclass(frozen=True)
class MarketListingDetails:
    id: int
    item_id: int
    item_name: str
    item_type: str
    meta_json: dict[str, Any]
    qty: int
    price: int
    seller_user_id: int
    seller_tg_id: int
    seller_username: str
    created_at: str


@dataclass(frozen=True)
class MarketPage:
    page: int
    page_size: int
    total: int
    max_page: int
    listings: list[MarketListingView]
    has_prev: bool
    has_next: bool


@dataclass(frozen=True)
class SellInventoryItemView:
    item_id: int
    name: str
    item_type: str
    qty: int
    tier: str


@dataclass(frozen=True)
class UserActiveListingView:
    id: int
    item_id: int
    item_name: str
    item_type: str
    qty: int
    price: int
    tier: str


@dataclass(frozen=True)
class WithdrawResult:
    listing_id: int
    item_id: int
    item_name: str
    qty: int




@dataclass(frozen=True)
class MarketItemView:
    item_id: int
    item_name: str
    item_type: str
    tier: str
    lots_count: int
    qty_total: int
    price_min: int
    price_max: int


@dataclass(frozen=True)
class MarketItemsPage:
    page: int
    page_size: int
    total: int
    max_page: int
    items: list[MarketItemView]
    has_prev: bool
    has_next: bool


@dataclass(frozen=True)
class MarketItemLotsPage:
    item_id: int
    item_name: str
    item_type: str
    tier: str
    page: int
    page_size: int
    total: int
    max_page: int
    listings: list[MarketListingView]
    has_prev: bool
    has_next: bool

class MarketService:
    def __init__(self, session: AsyncSession):
        self._s = session
        self._username_col_checked = False
        self._username_col: str | None = None

        self._weapons_cols_checked = False
        self._weapons_cols: set[str] = set()

    async def _ensure_user_id(self, tg_id: int) -> int:
        row = (
            await self._s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg"),
                {"tg": int(tg_id)},
            )
        ).first()
        if row:
            return int(row[0])

        row = (
            await self._s.execute(
                text("INSERT INTO users (tg_id) VALUES (:tg) RETURNING id"),
                {"tg": int(tg_id)},
            )
        ).first()
        await self._s.commit()
        if not row:
            raise MarketError("Не удалось создать пользователя.")
        return int(row[0])

    async def _get_character_owned(self, tg_id: int, character_id: int) -> Mapping[str, Any]:
        uid = await self._ensure_user_id(tg_id)
        ch = (
            await self._s.execute(
                text(
                    """
                    SELECT id, name
                    FROM characters
                    WHERE id = :cid AND user_id = :uid
                    """
                ),
                {"cid": int(character_id), "uid": int(uid)},
            )
        ).mappings().first()
        if not ch:
            raise MarketError("Персонаж не найден.")
        return ch

    async def _detect_username_col(self) -> str | None:
        if self._username_col_checked:
            return self._username_col

        self._username_col_checked = True
        self._username_col = None

        candidates = ["username", "tg_username", "telegram_username"]
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'users'
                      AND column_name = ANY(:cols)
                    """
                ),
                {"cols": candidates},
            )
        ).mappings().all()

        existing = {str(x.get("column_name") or "").strip() for x in (r or []) if str(x.get("column_name") or "").strip()}
        for c in candidates:
            if c in existing:
                self._username_col = c
                break

        return self._username_col

    async def _get_username_for_user_id(self, user_id: int) -> str:
        col = await self._detect_username_col()
        if not col:
            return ""

        if col not in {"username", "tg_username", "telegram_username"}:
            return ""

        row = (
            await self._s.execute(
                text(f"SELECT COALESCE({col}, '') AS u FROM users WHERE id = :uid LIMIT 1"),
                {"uid": int(user_id)},
            )
        ).mappings().first()

        s = str((row or {}).get("u") or "").strip()
        if not s:
            return ""

        if s.startswith("@"):
            return s
        return "@" + s

    async def _ensure_weapons_cols(self) -> set[str]:
        if self._weapons_cols_checked:
            return set(self._weapons_cols)

        self._weapons_cols_checked = True
        self._weapons_cols = set()

        cols = (
            await self._s.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'weapons'
                    """
                )
            )
        ).mappings().all()

        self._weapons_cols = {str(x.get("column_name") or "").strip() for x in (cols or []) if str(x.get("column_name") or "").strip()}
        return set(self._weapons_cols)

    async def _weapon_name_by_unique_id(self, unique_id: int) -> str:
        uid = int(unique_id or 0)
        if uid <= 0:
            return ""

        r = (
            await self._s.execute(
                text("SELECT name FROM weapons WHERE id = :id LIMIT 1"),
                {"id": uid},
            )
        ).mappings().first()
        if r and str(r.get("name") or "").strip():
            return str(r.get("name") or "").strip()

        cols = await self._ensure_weapons_cols()

        if "legacy_id" in cols:
            r = (
                await self._s.execute(
                    text("SELECT name FROM weapons WHERE legacy_id = :id LIMIT 1"),
                    {"id": uid},
                )
            ).mappings().first()
            if r and str(r.get("name") or "").strip():
                return str(r.get("name") or "").strip()

        if "weapon_legacy_id" in cols:
            r = (
                await self._s.execute(
                    text("SELECT name FROM weapons WHERE weapon_legacy_id = :id LIMIT 1"),
                    {"id": uid},
                )
            ).mappings().first()
            if r and str(r.get("name") or "").strip():
                return str(r.get("name") or "").strip()

        return ""

    async def _filter_out_unique_weapon_names(self, names: list[str]) -> list[str]:
        cleaned = [str(x).strip() for x in (names or []) if str(x).strip()]
        if not cleaned:
            return []

        r = (
            await self._s.execute(
                text(
                    """
                    SELECT name
                    FROM items
                    WHERE name = ANY(:names)
                      AND (meta_json->>'kind') = 'unique_weapon'
                    """
                ),
                {"names": cleaned},
            )
        ).mappings().all()

        uniq = {str(x.get("name") or "").strip() for x in (r or []) if str(x.get("name") or "").strip()}
        if not uniq:
            return cleaned

        return [n for n in cleaned if n not in uniq]

    @staticmethod
    def _infer_weapon_name_from_item_name(item_name: str) -> str:
        s = str(item_name or "").strip()
        if not s:
            return ""
        for sep in [" – ", " — ", " - "]:
            if sep in s:
                left = s.split(sep, 1)[0].strip()
                return left
        return ""

    async def _list_weapons_names_by_caliber(self, caliber_id: int, *, limit: int = 25) -> list[str]:
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT name
                    FROM weapons
                    WHERE caliber_id = :cid
                    ORDER BY name
                    LIMIT :lim
                    """
                ),
                {"cid": int(caliber_id), "lim": int(limit)},
            )
        ).mappings().all()

        names = [str(x.get("name") or "").strip() for x in (rows or []) if str(x.get("name") or "").strip()]
        return await self._filter_out_unique_weapon_names(names)

    async def _list_weapons_names_by_categories(self, cats: list[Any], *, limit: int = 25) -> list[str]:
        cleaned = [str(x).strip() for x in (cats or []) if str(x).strip()]
        if not cleaned:
            return []

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT name
                    FROM weapons
                    WHERE category = ANY(:cats)
                    ORDER BY name
                    LIMIT :lim
                    """
                ),
                {"cats": cleaned, "lim": int(limit)},
            )
        ).mappings().all()

        names = [str(x.get("name") or "").strip() for x in (rows or []) if str(x.get("name") or "").strip()]
        return await self._filter_out_unique_weapon_names(names)

    async def active_count(self, *, exclude_user_id: int | None = None) -> int:
        sql = """
        SELECT COUNT(*) AS cnt
        FROM market_listings
        WHERE status = 'active'
        """
        params: dict[str, Any] = {}
        if exclude_user_id is not None:
            sql += "\n  AND seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        r = (await self._s.execute(text(sql), params)).mappings().first()
        return int((r or {}).get("cnt") or 0)

    async def list_active(
        self,
        *,
        limit: int = 30,
        offset: int = 0,
        exclude_user_id: int | None = None,
    ) -> list[MarketListingView]:
        sql = """
        SELECT
          ml.id,
          ml.item_id,
          ml.qty,
          ml.price,
          i.name AS item_name,
          i.item_type AS item_type,
          COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
        FROM market_listings ml
        JOIN items i ON i.id = ml.item_id
        LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
        LEFT JOIN weapon_mods wm ON wm.item_id = i.id
        WHERE ml.status = 'active'
        """
        params: dict[str, Any] = {"lim": int(limit), "off": int(offset)}
        if exclude_user_id is not None:
            sql += "\n  AND ml.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        sql += "\nORDER BY ml.created_at DESC, ml.id DESC\nLIMIT :lim OFFSET :off"

        rows = (await self._s.execute(text(sql), params)).mappings().all()

        out: list[MarketListingView] = []
        for r in rows:
            out.append(
                MarketListingView(
                    id=int(r.get("id") or 0),
                    item_id=int(r.get("item_id") or 0),
                    item_name=str(r.get("item_name") or "Предмет"),
                    item_type=str(r.get("item_type") or "misc"),
                    qty=int(r.get("qty") or 1),
                    price=int(r.get("price") or 0),
                    tier=str(r.get("tier") or "").strip(),
                )
            )
        return out

    async def get_page(self, *, page: int, page_size: int = 30, exclude_user_id: int | None = None) -> MarketPage:
        total = await self.active_count(exclude_user_id=exclude_user_id)
        if page_size <= 0:
            page_size = 30

        max_page = 0
        if total > 0:
            max_page = (total - 1) // page_size

        page = max(0, int(page))
        if page > max_page:
            page = max_page

        offset = page * page_size
        listings = await self.list_active(limit=page_size, offset=offset, exclude_user_id=exclude_user_id)

        return MarketPage(
            page=page,
            page_size=page_size,
            total=total,
            max_page=max_page,
            listings=list(listings),
            has_prev=page > 0,
            has_next=page < max_page,
        )


    async def active_items_count(self, *, exclude_user_id: int | None = None) -> int:
        sql = """
        SELECT COUNT(DISTINCT ml.item_id) AS cnt
        FROM market_listings ml
        WHERE ml.status = 'active'
        """
        params: dict[str, Any] = {}
        if exclude_user_id is not None:
            sql += "\n  AND ml.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        r = (await self._s.execute(text(sql), params)).mappings().first()
        return int((r or {}).get("cnt") or 0)

    async def list_active_items(
        self,
        *,
        limit: int = 30,
        offset: int = 0,
        exclude_user_id: int | None = None,
    ) -> list[MarketItemView]:
        tier_expr = "COALESCE(ies.tier, wm.tier, i.quality_tier, '')"
        sql = f"""
        SELECT
          ml.item_id,
          i.name AS item_name,
          i.item_type AS item_type,
          {tier_expr} AS tier,
          COUNT(*) AS lots_count,
          COALESCE(SUM(ml.qty), 0) AS qty_total,
          COALESCE(MIN(ml.price), 0) AS price_min,
          COALESCE(MAX(ml.price), 0) AS price_max
        FROM market_listings ml
        JOIN items i ON i.id = ml.item_id
        LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
        LEFT JOIN weapon_mods wm ON wm.item_id = i.id
        WHERE ml.status = 'active'
        """
        params: dict[str, Any] = {"lim": int(limit), "off": int(offset)}
        if exclude_user_id is not None:
            sql += "\n  AND ml.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        sql += f"\nGROUP BY ml.item_id, i.name, i.item_type, {tier_expr}\nORDER BY i.name\nLIMIT :lim OFFSET :off"

        rows = (await self._s.execute(text(sql), params)).mappings().all()

        out: list[MarketItemView] = []
        for r in rows:
            out.append(
                MarketItemView(
                    item_id=int(r.get("item_id") or 0),
                    item_name=str(r.get("item_name") or "Предмет"),
                    item_type=str(r.get("item_type") or "misc"),
                    tier=str(r.get("tier") or "").strip(),
                    lots_count=int(r.get("lots_count") or 0),
                    qty_total=int(r.get("qty_total") or 0),
                    price_min=int(r.get("price_min") or 0),
                    price_max=int(r.get("price_max") or 0),
                )
            )
        return out

    async def get_items_page(
        self,
        *,
        page: int,
        page_size: int = 30,
        exclude_user_id: int | None = None,
    ) -> MarketItemsPage:
        total = await self.active_items_count(exclude_user_id=exclude_user_id)
        if page_size <= 0:
            page_size = 30

        max_page = 0
        if total > 0:
            max_page = (total - 1) // page_size

        page = max(0, int(page))
        if page > max_page:
            page = max_page

        offset = page * page_size
        items = await self.list_active_items(limit=page_size, offset=offset, exclude_user_id=exclude_user_id)

        return MarketItemsPage(
            page=page,
            page_size=page_size,
            total=total,
            max_page=max_page,
            items=list(items),
            has_prev=page > 0,
            has_next=page < max_page,
        )

    @staticmethod
    def _price_range_label(min_price: int, max_price: int) -> str:
        a = max(0, int(min_price))
        b = max(0, int(max_price))
        if a == b:
            return str(a)
        return f"{a}–{b}"

    def render_market_items_list(self, items: list[MarketItemView]) -> str:
        if not items:
            return "Товаров нет"

        lines: list[str] = []
        for idx, it in enumerate(items, start=1):
            item_id = int(it.item_id or 0)
            lots = max(0, int(it.lots_count or 0))
            qty_total = max(0, int(it.qty_total or 0))
            price_label = self._price_range_label(int(it.price_min or 0), int(it.price_max or 0))

            name = _esc(str(it.item_name or "Предмет"))
            name = _link_name(item_id, name)

            item_type_raw = str(it.item_type or "misc").strip()
            is_misc = item_type_raw.lower() == "misc"
            tier = str(it.tier or "").strip()

            base = f"{idx}. {name} – лотов {lots} – ×{qty_total} – {price_label}"

            if is_misc:
                lines.append(base + f" – {_esc(item_type_raw)} –")
                continue

            if tier:
                lines.append(base + f" – {_esc(tier)} – {_esc(item_type_raw)}")
            else:
                lines.append(base + f" – {_esc(item_type_raw)}")

        return "\n".join(lines).rstrip()

    async def market_items_text(
        self,
        *,
        page: int,
        page_size: int = 30,
        exclude_user_id: int | None = None,
    ) -> tuple[str, MarketItemsPage]:
        mp = await self.get_items_page(page=int(page), page_size=page_size, exclude_user_id=exclude_user_id)

        page_label = f"{mp.page + 1}/{mp.max_page + 1}" if (mp.total > 0) else "1/1"

        start = 0
        end = 0
        if mp.total > 0 and mp.items:
            start = mp.page * mp.page_size + 1
            end = start + len(mp.items) - 1

        if mp.total <= 0:
            range_label = "0-0"
        else:
            range_label = f"{start}-{end}" if (start and end) else "0-0"

        parts = [
            "<b>Рынок</b>",
            f"Страница {page_label} – {range_label} из {mp.total}",
            self.render_market_items_list(mp.items),
        ]
        return ("\n".join(parts).rstrip(), mp)

    async def _item_lots_aggregate(
        self,
        item_id: int,
        *,
        exclude_user_id: int | None = None,
    ) -> dict[str, int]:
        sql = """
        SELECT
          COUNT(*) AS lots_count,
          COALESCE(SUM(ml.qty), 0) AS qty_total,
          COALESCE(MIN(ml.price), 0) AS price_min,
          COALESCE(MAX(ml.price), 0) AS price_max
        FROM market_listings ml
        WHERE ml.status = 'active' AND ml.item_id = :iid
        """
        params: dict[str, Any] = {"iid": int(item_id)}
        if exclude_user_id is not None:
            sql += "\n  AND ml.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        r = (await self._s.execute(text(sql), params)).mappings().first() or {}
        return {
            "lots_count": int(r.get("lots_count") or 0),
            "qty_total": int(r.get("qty_total") or 0),
            "price_min": int(r.get("price_min") or 0),
            "price_max": int(r.get("price_max") or 0),
        }

    async def list_active_lots_for_item(
        self,
        item_id: int,
        *,
        limit: int = 30,
        offset: int = 0,
        exclude_user_id: int | None = None,
    ) -> list[MarketListingView]:
        sql = """
        SELECT
          ml.id,
          ml.item_id,
          ml.qty,
          ml.price,
          i.name AS item_name,
          i.item_type AS item_type,
          COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
        FROM market_listings ml
        JOIN items i ON i.id = ml.item_id
        LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
        LEFT JOIN weapon_mods wm ON wm.item_id = i.id
        WHERE ml.status = 'active'
          AND ml.item_id = :iid
        """
        params: dict[str, Any] = {"iid": int(item_id), "lim": int(limit), "off": int(offset)}
        if exclude_user_id is not None:
            sql += "\n  AND ml.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        sql += "\nORDER BY ml.price ASC, ml.created_at DESC, ml.id DESC\nLIMIT :lim OFFSET :off"

        rows = (await self._s.execute(text(sql), params)).mappings().all()

        out: list[MarketListingView] = []
        for r in rows:
            out.append(
                MarketListingView(
                    id=int(r.get("id") or 0),
                    item_id=int(r.get("item_id") or 0),
                    item_name=str(r.get("item_name") or "Предмет"),
                    item_type=str(r.get("item_type") or "misc"),
                    qty=int(r.get("qty") or 1),
                    price=int(r.get("price") or 0),
                    tier=str(r.get("tier") or "").strip(),
                )
            )
        return out

    async def get_item_lots_page(
        self,
        item_id: int,
        *,
        page: int,
        page_size: int = 30,
        exclude_user_id: int | None = None,
    ) -> MarketItemLotsPage:
        item_id = int(item_id or 0)
        if item_id <= 0:
            return MarketItemLotsPage(
                item_id=0,
                item_name="Предмет",
                item_type="misc",
                tier="",
                page=0,
                page_size=page_size,
                total=0,
                max_page=0,
                listings=[],
                has_prev=False,
                has_next=False,
            )

        agg = await self._item_lots_aggregate(item_id, exclude_user_id=exclude_user_id)
        total = int(agg.get("lots_count") or 0)

        if page_size <= 0:
            page_size = 30

        max_page = 0
        if total > 0:
            max_page = (total - 1) // page_size

        page = max(0, int(page))
        if page > max_page:
            page = max_page

        offset = page * page_size
        listings = await self.list_active_lots_for_item(item_id, limit=page_size, offset=offset, exclude_user_id=exclude_user_id)

        base = listings[0] if listings else None
        item_name = str(getattr(base, "item_name", "") or "").strip()
        item_type = str(getattr(base, "item_type", "") or "").strip()
        tier = str(getattr(base, "tier", "") or "").strip()

        if not item_name or not item_type:
            r = (
                await self._s.execute(
                    text(
                        """
                        SELECT
                          i.name AS item_name,
                          i.item_type AS item_type,
                          COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
                        FROM items i
                        LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
                        LEFT JOIN weapon_mods wm ON wm.item_id = i.id
                        WHERE i.id = :iid
                        LIMIT 1
                        """
                    ),
                    {"iid": int(item_id)},
                )
            ).mappings().first() or {}

            item_name = str(r.get("item_name") or "Предмет")
            item_type = str(r.get("item_type") or "misc")
            tier = str(r.get("tier") or "").strip()

        return MarketItemLotsPage(
            item_id=item_id,
            item_name=item_name,
            item_type=item_type,
            tier=tier,
            page=page,
            page_size=page_size,
            total=total,
            max_page=max_page,
            listings=list(listings),
            has_prev=page > 0,
            has_next=page < max_page,
        )

    def render_item_lots_list(self, listings: list[MarketListingView]) -> str:
        if not listings:
            return "Лотов нет"

        lines: list[str] = []
        for idx, l in enumerate(listings, start=1):
            qty = max(1, int(l.qty))
            price = max(0, int(l.price))
            lines.append(f"{idx}. Лот #{int(l.id)} – ×{qty} – {price}")
        return "\n".join(lines).rstrip()

    async def market_item_lots_text(
        self,
        item_id: int,
        *,
        page: int,
        page_size: int = 30,
        exclude_user_id: int | None = None,
    ) -> tuple[str, MarketItemLotsPage]:
        mp = await self.get_item_lots_page(int(item_id), page=int(page), page_size=page_size, exclude_user_id=exclude_user_id)

        page_label = f"{mp.page + 1}/{mp.max_page + 1}" if (mp.total > 0) else "1/1"

        start = 0
        end = 0
        if mp.total > 0 and mp.listings:
            start = mp.page * mp.page_size + 1
            end = start + len(mp.listings) - 1

        if mp.total <= 0:
            range_label = "0-0"
        else:
            range_label = f"{start}-{end}" if (start and end) else "0-0"

        agg = await self._item_lots_aggregate(int(mp.item_id), exclude_user_id=exclude_user_id)
        qty_total = int(agg.get("qty_total") or 0)
        price_label = self._price_range_label(int(agg.get("price_min") or 0), int(agg.get("price_max") or 0))

        item_title = _esc(str(mp.item_name or "Предмет"))
        item_title = _link_name(int(mp.item_id), item_title)

        lines: list[str] = []
        lines.append(f"<b>Рынок – {item_title}</b>")
        lines.append(f"Страница {page_label} – {range_label} из {mp.total} лотов")
        lines.append(f"Всего: ×{qty_total} • Цена: {price_label}")
        lines.append(self.render_item_lots_list(mp.listings))

        return ("\n".join(lines).rstrip(), mp)

    def render_market_list(self, listings: list[MarketListingView]) -> str:
        if not listings:
            return "Лотов нет"

        lines: list[str] = []
        for idx, l in enumerate(listings, start=1):
            qty = max(1, int(l.qty))
            price = max(0, int(l.price))
            name = _esc(str(l.item_name or "Предмет"))
            name = _link_name(getattr(l, "item_id", None), name)

            item_type_raw = str(l.item_type or "misc").strip()
            is_misc = item_type_raw.lower() == "misc"
            tier = str(l.tier or "").strip()

            if is_misc:
                lines.append(f"{idx}. {name} – ×{qty} – {price} – {_esc(item_type_raw)} –")
                continue

            if tier:
                lines.append(f"{idx}. {name} – ×{qty} – {price} – {_esc(tier)} – {_esc(item_type_raw)}")
            else:
                lines.append(f"{idx}. {name} – ×{qty} – {price} – {_esc(item_type_raw)}")

        return "\n".join(lines).rstrip()

    def render_market_table(self, listings: list[MarketListingView]) -> str:
        if not listings:
            return "<pre>Лотов нет</pre>"

        IDX_W = 2
        ITEM_W = 30
        TIER_W = 3
        QTY_W = 4
        PRICE_W = 7
        TYPE_W = 12

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Предмет", ITEM_W),
                _cell("Тир", TIER_W),
                _cell("Кол", QTY_W, align="right"),
                _cell("Цена", PRICE_W, align="right"),
                _cell("Тип", TYPE_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * ITEM_W,
                "-" * TIER_W,
                "-" * QTY_W,
                "-" * PRICE_W,
                "-" * TYPE_W,
            ]
        )

        rows: list[str] = [header, sep]
        for idx, l in enumerate(listings, start=1):
            qty = max(1, int(l.qty))
            price = max(0, int(l.price))
            tier = (l.tier or "").strip()
            rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(l.item_name, ITEM_W),
                        _cell(tier if tier else "–", TIER_W),
                        _cell(qty, QTY_W, align="right"),
                        _cell(price, PRICE_W, align="right"),
                        _cell(l.item_type, TYPE_W),
                    ]
                )
            )

        return "<pre>" + "\n".join(rows) + "</pre>"

    async def market_text(
        self,
        *,
        page: int,
        page_size: int = 30,
        exclude_user_id: int | None = None,
    ) -> tuple[str, MarketPage]:
        mp = await self.get_page(page=page, page_size=page_size, exclude_user_id=exclude_user_id)

        page_label = f"{mp.page + 1}/{mp.max_page + 1}" if (mp.total > 0) else "1/1"

        start = 0
        end = 0
        if mp.total > 0 and mp.listings:
            start = mp.page * mp.page_size + 1
            end = start + len(mp.listings) - 1

        if mp.total <= 0:
            range_label = "0-0"
        else:
            range_label = f"{start}-{end}" if (start and end) else "0-0"

        parts = [
            "<b>Рынок</b>",
            f"Страница {page_label} – {range_label} из {mp.total}",
            self.render_market_list(mp.listings),
        ]
        return ("\n".join(parts).rstrip(), mp)

    async def _get_listing(self, listing_id: int) -> Optional[MarketListingDetails]:
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ml.id,
                      ml.item_id,
                      ml.qty,
                      ml.price,
                      ml.created_at,
                      ml.seller_user_id,
                      i.name AS item_name,
                      i.item_type AS item_type,
                      COALESCE(i.meta_json, '{}'::jsonb) AS meta_json,
                      u.tg_id AS seller_tg_id
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    JOIN users u ON u.id = ml.seller_user_id
                    WHERE ml.id = :lid
                    LIMIT 1
                    """
                ),
                {"lid": int(listing_id)},
            )
        ).mappings().first()

        if not r:
            return None

        meta = r.get("meta_json")
        meta_json = dict(meta) if isinstance(meta, Mapping) else {}

        seller_user_id = int(r.get("seller_user_id") or 0)
        seller_username = await self._get_username_for_user_id(seller_user_id)

        return MarketListingDetails(
            id=int(r.get("id") or 0),
            item_id=int(r.get("item_id") or 0),
            item_name=str(r.get("item_name") or "Предмет"),
            item_type=str(r.get("item_type") or "misc"),
            meta_json=meta_json,
            qty=int(r.get("qty") or 1),
            price=int(r.get("price") or 0),
            seller_user_id=seller_user_id,
            seller_tg_id=int(r.get("seller_tg_id") or 0),
            seller_username=seller_username,
            created_at=_fmt_date(r.get("created_at")),
        )

    async def _equipment_stats_text(self, item_id: int) -> str | None:
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT tier, armor, reliability, accuracy_bonus,
                           reaction_bonus, initiative_bonus, stealth_bonus,
                           carry_capacity_bonus, loot_analysis_bonus,
                           item_handling_bonus
                    FROM item_equipment_stats
                    WHERE item_id = :iid
                    """
                ),
                {"iid": int(item_id)},
            )
        ).mappings().first()

        if not r:
            return None

        def _n(v: Any) -> int:
            try:
                return int(v or 0)
            except Exception:
                return 0

        rows = [
            ("Тир", str(r.get("tier") or "D")),
            ("Броня", _n(r.get("armor"))),
            ("Надёжность", _n(r.get("reliability"))),
            ("Точность", _signed(_n(r.get("accuracy_bonus")), decimals=0)),
            ("Реакция", _signed(r.get("reaction_bonus"), decimals=1)),
            ("Инициатива", _signed(r.get("initiative_bonus"), decimals=1)),
            ("Скрытность", _signed(r.get("stealth_bonus"), decimals=1)),
            ("Грузоподъём", _signed(r.get("carry_capacity_bonus"), decimals=1)),
            ("Анализ лута", _signed(_n(r.get("loot_analysis_bonus")), decimals=0)),
            ("Обращение", _signed(_n(r.get("item_handling_bonus")), decimals=0)),
        ]
        rows = [(k, v) for (k, v) in rows if k == "Тир" or not _is_zero_value(v)]
        return _kv_text(rows)

    async def _ammo_details_compact(self, meta_json: dict[str, Any]) -> dict[str, Any] | None:
        ammo_type_id = meta_json.get("ammo_type_id")
        caliber_code = meta_json.get("caliber_code")
        bullet_type = meta_json.get("bullet_type")

        if ammo_type_id is not None:
            r = (
                await self._s.execute(
                    text(
                        """
                        SELECT a.id, a.name, a.damage, a.armor_penetration, c.code, c.name AS caliber_name, a.caliber_id
                        FROM ammo_types a
                        JOIN calibers c ON c.id = a.caliber_id
                        WHERE a.id = :aid
                        """
                    ),
                    {"aid": int(ammo_type_id)},
                )
            ).mappings().first()
        elif caliber_code and bullet_type:
            r = (
                await self._s.execute(
                    text(
                        """
                        SELECT a.id, a.name, a.damage, a.armor_penetration, c.code, c.name AS caliber_name, a.caliber_id
                        FROM ammo_types a
                        JOIN calibers c ON c.id = a.caliber_id
                        WHERE c.code = :cc AND a.name = :bt
                        LIMIT 1
                        """
                    ),
                    {"cc": str(caliber_code), "bt": str(bullet_type)},
                )
            ).mappings().first()
        else:
            return None

        if not r:
            return None

        caliber_name = str(r.get("caliber_name") or "").strip()
        if not caliber_name:
            caliber_name = str(r.get("code") or "–").strip() or "–"

        ammo_name = str(r.get("name") or "–").strip() or "–"
        damage = int(r.get("damage") or 0)
        armor_pen = int(r.get("armor_penetration") or 0)
        caliber_id = int(r.get("caliber_id") or 0)

        names = await self._list_weapons_names_by_caliber(caliber_id, limit=25)

        return {
            "caliber": caliber_name,
            "ammo_type": ammo_name,
            "damage": damage,
            "armor_pen": armor_pen,
            "weapons_names": names,
        }

    async def _weapon_mod_details_compact(self, item_id: int) -> dict[str, Any] | None:
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT mod_type, tier, compatible_categories, unique_weapon_id, slot_limit,
                           accuracy_bonus, reliability_bonus, damage_bonus, armor_pen_bonus
                    FROM weapon_mods
                    WHERE item_id = :iid
                    """
                ),
                {"iid": int(item_id)},
            )
        ).mappings().first()

        if not r:
            return None

        cats = list(r.get("compatible_categories") or [])

        mod_type = str(r.get("mod_type") or "–").strip() or "–"
        tier = str(r.get("tier") or "D").strip() or "D"
        slot_limit = int(r.get("slot_limit") or 1)

        acc_b = int(r.get("accuracy_bonus") or 0)
        rel_b = int(r.get("reliability_bonus") or 0)
        dmg_b = int(r.get("damage_bonus") or 0)
        pen_b = int(r.get("armor_pen_bonus") or 0)

        unique_weapon_id: int | None = None
        try:
            uwid = int(r.get("unique_weapon_id") or 0)
        except Exception:
            uwid = 0
        if uwid > 0:
            unique_weapon_id = uwid

        names: list[str] = []

        if cats:
            names = await self._list_weapons_names_by_categories(cats, limit=25)

        return {
            "mod_type": mod_type,
            "tier": tier,
            "slot_limit": slot_limit,
            "accuracy_bonus": acc_b,
            "reliability_bonus": rel_b,
            "damage_bonus": dmg_b,
            "armor_pen_bonus": pen_b,
            "unique_weapon_id": unique_weapon_id,
            "categories": [str(x).strip() for x in cats if str(x).strip()],
            "weapons_names": names,
        }

    def _details_header(self, l: MarketListingDetails) -> list[str]:
        qty = max(1, int(l.qty))
        seller = l.seller_username.strip() if str(l.seller_username or "").strip() else _mask_tg(l.seller_tg_id)
        date_only = str(l.created_at or "").strip()

        header_line = f"{_esc(l.item_name)} ×{qty}"
        info_bits = [f"Лот: <b>#{l.id}</b>", f"Продавец: <b>{_esc(seller)}</b>"]
        if date_only:
            info_bits.append(_esc(date_only))

        return [
            "<b>Рынок – подробнее</b>",
            header_line,
            _price_line(int(l.price)),
            " • ".join(info_bits),
        ]

    async def listing_details_text(self, listing_id: int) -> str:
        l = await self._get_listing(int(listing_id))
        if not l:
            return "<b>Рынок – подробнее</b>\n\nЛот не найден."

        it = (l.item_type or "").lower()
        kind = str(l.meta_json.get("kind") or "").lower()

        is_equipment = it in {"head", "body", "gloves", "boots"}
        is_ammo = it == "ammo" or kind == "ammo"
        is_mod = it in {"weapon upgrade", "weapon_upgrade"}
        is_weapon = it == "weapon" or kind == "weapon"

        parts: list[str] = self._details_header(l)

        if is_ammo:
            info = await self._ammo_details_compact(l.meta_json)
            parts.append("")
            parts.append("<b>Патроны</b>")
            if info:
                parts.append(
                    f"Калибр: <b>{_esc(str(info.get('caliber') or '–'))}</b> • Тип: <b>{_esc(str(info.get('ammo_type') or '–'))}</b>")
                parts.append(
                    f"Урон: <b>{int(info.get('damage') or 0)}</b> • Пробитие: <b>{int(info.get('armor_pen') or 0)}</b>")
                parts.append("")
                parts.append("<b>Подходит к оружию</b>")
                parts.append(_bullet_list(list(info.get("weapons_names") or []), limit=25))
            else:
                parts.append("нет данных")
            return "\n".join(parts).rstrip()

        if is_equipment:
            txt = await self._equipment_stats_text(l.item_id)
            parts.append("")
            parts.append("<b>Экипировка</b>")
            parts.append(txt if txt else "нет данных")
            return "\n".join(parts).rstrip()

        mod_info: dict[str, Any] | None = None
        if is_mod:
            mod_info = await self._weapon_mod_details_compact(l.item_id)
        else:
            mod_info = await self._weapon_mod_details_compact(l.item_id)
            if mod_info:
                is_mod = True

        if is_mod:
            parts.append("")
            parts.append("<b>Апгрейд оружия</b>")
            if mod_info:
                line_a = (
                    f"Тип: <b>{_esc(str(mod_info.get('mod_type') or '–'))}</b> • "
                    f"Тир: <b>{_esc(str(mod_info.get('tier') or 'D'))}</b> • "
                    f"Лимит: <b>{int(mod_info.get('slot_limit') or 1)}</b>"
                )
                parts.append(line_a)

                bonus_bits: list[str] = []
                if int(mod_info.get("accuracy_bonus") or 0) != 0:
                    bonus_bits.append(f"Точность {int(mod_info.get('accuracy_bonus') or 0):+d}")
                if int(mod_info.get("reliability_bonus") or 0) != 0:
                    bonus_bits.append(f"Надёжность {int(mod_info.get('reliability_bonus') or 0):+d}")
                if int(mod_info.get("damage_bonus") or 0) != 0:
                    bonus_bits.append(f"Урон {int(mod_info.get('damage_bonus') or 0):+d}")
                if int(mod_info.get("armor_pen_bonus") or 0) != 0:
                    bonus_bits.append(f"Пробитие {int(mod_info.get('armor_pen_bonus') or 0):+d}")

                if bonus_bits:
                    parts.append(" • ".join([_esc(x) for x in bonus_bits]))

                cats = list(mod_info.get("categories") or [])
                if cats:
                    parts.append(f"Категории: <b>{_esc(', '.join([str(x) for x in cats]))}</b>")

                unique_weapon_id = int(mod_info.get("unique_weapon_id") or 0)
                is_unique_mod = (kind == "unique_weapon") or (unique_weapon_id > 0)

                if is_unique_mod:
                    wname = ""
                    if unique_weapon_id > 0:
                        wname = await self._weapon_name_by_unique_id(unique_weapon_id)
                    if not wname:
                        wname = self._infer_weapon_name_from_item_name(l.item_name)
                    names = [wname] if wname else []
                else:
                    names = list(mod_info.get("weapons_names") or [])

                if names:
                    parts.append("")
                    parts.append("<b>Подходит к оружию</b>")
                    parts.append(_bullet_list(names, limit=25))
            else:
                parts.append("нет данных")
            return "\n".join(parts).rstrip()

        if is_weapon:
            parts.append("")
            parts.append("<b>Оружие</b>")
            parts.append("нет данных")
            return "\n".join(parts).rstrip()

        parts.append("")
        parts.append("В разработке")
        return "\n".join(parts).rstrip()

    async def sellable_inventory(
        self,
        tg_id: int,
        character_id: int,
        *,
        limit: int = 30,
        offset: int = 0,
    ) -> tuple[str, list[SellInventoryItemView]]:
        ch = await self._get_character_owned(tg_id, character_id)

        total = (
            await self._s.execute(
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
        total_cnt = int((total or {}).get("cnt") or 0)

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ci.item_id, ci.qty, i.name, i.item_type,
                      COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
                    LEFT JOIN weapon_mods wm ON wm.item_id = i.id
                    WHERE ci.character_id = :cid
                      AND ci.qty > 0
                      AND NOT EXISTS (
                        SELECT 1
                        FROM equipment e
                        WHERE e.character_id = ci.character_id
                          AND ci.item_id IN (e.head_item_id, e.body_item_id, e.gloves_item_id, e.boots_item_id)
                      )
                    ORDER BY i.name
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"cid": int(character_id), "lim": int(limit), "off": int(offset)},
            )
        ).mappings().all()

        items: list[SellInventoryItemView] = []
        for r in rows:
            items.append(
                SellInventoryItemView(
                    item_id=int(r.get("item_id") or 0),
                    qty=int(r.get("qty") or 0),
                    name=str(r.get("name") or "Предмет"),
                    item_type=str(r.get("item_type") or "misc"),
                    tier=str(r.get("tier") or "").strip(),
                )
            )

        title = f"<b>Выставить на рынок</b> – {_esc(str(ch.get('name') or 'Персонаж'))}"
        if not items:
            return title + "\nНет предметов для выставления.", items

        if limit <= 0:
            limit = 30
        page = max(0, int(offset) // int(limit))
        pages = 1
        if total_cnt > 0:
            pages = (total_cnt - 1) // int(limit) + 1
        page_label = f"{page + 1}/{pages}"

        IDX_W = 2
        ITEM_W = 30
        TIER_W = 3
        QTY_W = 4
        TYPE_W = 12

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Предмет", ITEM_W),
                _cell("Тир", TIER_W),
                _cell("Кол", QTY_W, align="right"),
                _cell("Тип", TYPE_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * ITEM_W,
                "-" * TIER_W,
                "-" * QTY_W,
                "-" * TYPE_W,
            ]
        )

        table_rows: list[str] = [header, sep]
        for idx, it in enumerate(items, start=1):
            tier = (it.tier or "").strip()
            table_rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(it.name, ITEM_W),
                        _cell(tier if tier else "–", TIER_W),
                        _cell(max(1, int(it.qty)), QTY_W, align="right"),
                        _cell(it.item_type, TYPE_W),
                    ]
                )
            )

        shown = min(len(items), int(limit))
        info = f"Доступно: {total_cnt} | Показано: {shown} | Страница: {page_label}"
        hint = "Напиши № предмета из таблицы."

        text_out = "\n".join(
            [
                title,
                info,
                "<pre>" + "\n".join(table_rows) + "</pre>",
                hint,
            ]
        ).rstrip()

        if len(text_out) > 3900:
            text_out = "\n".join([title, info, hint]).rstrip()

        return text_out, items

    async def create_listing_from_character(
        self,
        tg_id: int,
        character_id: int,
        item_id: int,
        qty: int,
        price: int,
    ) -> int:
        uid = await self._ensure_user_id(tg_id)
        await self._get_character_owned(tg_id, character_id)

        if qty <= 0:
            raise MarketError("Некорректное количество.")
        if price < 0:
            raise MarketError("Некорректная цена.")

        eq = (
            await self._s.execute(
                text(
                    """
                    SELECT head_item_id, body_item_id, gloves_item_id, boots_item_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).first()

        if eq:
            equipped = {x for x in eq if x is not None}
            if int(item_id) in equipped:
                raise MarketError("Этот предмет надет на бойце.")

        inv = (
            await self._s.execute(
                text(
                    """
                    SELECT qty
                    FROM character_inventory
                    WHERE character_id = :cid AND item_id = :iid
                    FOR UPDATE
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id)},
            )
        ).first()

        if not inv:
            raise MarketError("Предмет не найден в инвентаре.")

        have = int(inv[0] or 0)
        if have < qty:
            raise MarketError("Недостаточно предметов.")

        new_qty = have - qty
        if new_qty > 0:
            await self._s.execute(
                text(
                    """
                    UPDATE character_inventory
                    SET qty = :q
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"q": int(new_qty), "cid": int(character_id), "iid": int(item_id)},
            )
        else:
            await self._s.execute(
                text(
                    """
                    DELETE FROM character_inventory
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id)},
            )

        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO market_listings (seller_user_id, item_id, qty, price, status)
                    VALUES (:uid, :iid, :qty, :price, 'active')
                    RETURNING id
                    """
                ),
                {"uid": int(uid), "iid": int(item_id), "qty": int(qty), "price": int(price)},
            )
        ).first()

        if not row:
            raise MarketError("Не удалось выставить лот.")

        await self._s.commit()
        return int(row[0])

    async def withdrawable_listings_text(
        self,
        tg_id: int,
        character_id: int,
        *,
        limit: int = 30,
        offset: int = 0,
    ) -> tuple[str, list[UserActiveListingView]]:
        uid = await self._ensure_user_id(tg_id)
        ch = await self._get_character_owned(tg_id, character_id)

        total_row = (
            await self._s.execute(
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

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                        ml.id,
                        ml.item_id,
                        i.name,
                        i.item_type,
                        ml.qty,
                        ml.price,
                        COALESCE(ies.tier, wm.tier, i.quality_tier, '') AS tier
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    LEFT JOIN item_equipment_stats ies ON ies.item_id = i.id
                    LEFT JOIN weapon_mods wm ON wm.item_id = i.id
                    WHERE ml.seller_user_id = :uid AND ml.status = 'active'
                    ORDER BY ml.created_at DESC, ml.id DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"uid": int(uid), "lim": int(limit), "off": int(offset)},
            )
        ).mappings().all()

        listings: list[UserActiveListingView] = []
        for r in rows:
            listings.append(
                UserActiveListingView(
                    id=int(r.get("id") or 0),
                    item_id=int(r.get("item_id") or 0),
                    item_name=str(r.get("name") or ""),
                    item_type=str(r.get("item_type") or "misc"),
                    qty=int(r.get("qty") or 0),
                    price=int(r.get("price") or 0),
                    tier=str(r.get("tier") or "").strip(),
                )
            )

        title = f"<b>Снять с продажи</b> – {_esc(str(ch.get('name') or 'Персонаж'))}"
        if total_cnt <= 0:
            return title + "\nНет активных лотов.", listings

        if limit <= 0:
            limit = 30
        page = max(0, int(offset) // int(limit))
        pages = 1
        if total_cnt > 0:
            pages = (total_cnt - 1) // int(limit) + 1
        page_label = f"{page + 1}/{pages}"

        IDX_W = 2
        ITEM_W = 30
        TIER_W = 3
        QTY_W = 4
        PRICE_W = 8
        TYPE_W = 12

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Предмет", ITEM_W),
                _cell("Тир", TIER_W),
                _cell("Кол", QTY_W, align="right"),
                _cell("Цена", PRICE_W, align="right"),
                _cell("Тип", TYPE_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * ITEM_W,
                "-" * TIER_W,
                "-" * QTY_W,
                "-" * PRICE_W,
                "-" * TYPE_W,
            ]
        )

        table_rows: list[str] = [header, sep]
        for idx, l2 in enumerate(listings, start=1):
            tier = (l2.tier or "").strip()
            table_rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(l2.item_name, ITEM_W),
                        _cell(tier if tier else "–", TIER_W),
                        _cell(max(1, int(l2.qty)), QTY_W, align="right"),
                        _cell(max(0, int(l2.price)), PRICE_W, align="right"),
                        _cell(l2.item_type, TYPE_W),
                    ]
                )
            )

        shown = min(len(listings), int(limit))
        info = f"Доступно: {total_cnt} | Показано: {shown} | Страница: {page_label}"
        hint = "Напиши № лота из таблицы, чтобы снять с продажи."

        text_out = "\n".join(
            [
                title,
                info,
                "<pre>" + "\n".join(table_rows) + "</pre>",
                hint,
            ]
        ).rstrip()

        if len(text_out) > 3900:
            text_out = "\n".join([title, info, hint]).rstrip()

        return text_out, listings

    async def withdraw_listing_to_character(
        self,
        tg_id: int,
        character_id: int,
        listing_id: int,
    ) -> WithdrawResult:
        uid = await self._ensure_user_id(tg_id)
        await self._get_character_owned(tg_id, character_id)

        row = (
            await self._s.execute(
                text(
                    """
                    SELECT ml.id, ml.item_id, i.name, ml.qty
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    WHERE ml.id = :lid AND ml.seller_user_id = :uid AND ml.status = 'active'
                    FOR UPDATE
                    """
                ),
                {"lid": int(listing_id), "uid": int(uid)},
            )
        ).first()

        if not row:
            raise MarketError("Лот не найден или уже снят.")

        lid = int(row[0])
        item_id = int(row[1])
        name = str(row[2] or "Предмет")
        qty = int(row[3] or 0)
        if qty <= 0:
            raise MarketError("Некорректное количество в лоте.")

        await self._s.execute(
            text(
                """
                UPDATE market_listings
                SET status = 'canceled'
                WHERE id = :lid
                """
            ),
            {"lid": int(lid)},
        )

        await self._s.execute(
            text(
                """
                INSERT INTO character_inventory (character_id, item_id, qty)
                VALUES (:cid, :iid, :qty)
                ON CONFLICT (character_id, item_id)
                DO UPDATE SET qty = character_inventory.qty + EXCLUDED.qty
                """
            ),
            {"cid": int(character_id), "iid": int(item_id), "qty": int(qty)},
        )

        await self._s.commit()
        return WithdrawResult(listing_id=lid, item_id=item_id, item_name=name, qty=qty)

    async def buy_listing_to_character(
        self,
        tg_id: int,
        character_id: int,
        listing_id: int,
        qty: int | None = None,
    ) -> PurchaseResult:
        buyer_uid = await self._ensure_user_id(tg_id)
        await self._get_character_owned(tg_id, character_id)

        row = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ml.id,
                      ml.item_id,
                      ml.qty,
                      ml.price,
                      ml.seller_user_id,
                      i.name AS item_name
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    WHERE ml.id = :lid AND ml.status = 'active'
                    FOR UPDATE
                    """
                ),
                {"lid": int(listing_id)},
            )
        ).mappings().first()

        if not row:
            raise MarketError("Лот не найден или уже недоступен.")

        lid = int(row.get("id") or 0)
        item_id = int(row.get("item_id") or 0)
        lot_qty = int(row.get("qty") or 0)
        lot_price = int(row.get("price") or 0)
        seller_uid = int(row.get("seller_user_id") or 0)
        item_name = str(row.get("item_name") or "Предмет")

        if lot_qty <= 0:
            raise MarketError("Некорректное количество в лоте.")
        if seller_uid == buyer_uid:
            raise MarketError("Это ваш лот. Используй Снять с продажи.")

        buy_qty = lot_qty if qty is None else int(qty)
        if buy_qty < 1:
            raise MarketError("Количество должно быть 1 или больше.")
        if buy_qty > lot_qty:
            raise MarketError(f"Доступно только {lot_qty}.")

        price = max(0, int((lot_price * buy_qty) // lot_qty))

        buyer = (
            await self._s.execute(
                text("SELECT balance FROM users WHERE id = :uid FOR UPDATE"),
                {"uid": int(buyer_uid)},
            )
        ).first()
        if not buyer:
            raise MarketError("Покупатель не найден.")
        buyer_balance = int(buyer[0] or 0)

        if buyer_balance < price:
            raise MarketError("Недостаточно монет.")

        fee = (price * int(MARKET_FEE_PCT)) // 100
        if fee < 0:
            fee = 0
        if fee > price:
            fee = price
        seller_gain = price - fee

        await self._s.execute(
            text("UPDATE users SET balance = balance - :p WHERE id = :uid"),
            {"p": int(price), "uid": int(buyer_uid)},
        )
        await self._s.execute(
            text("UPDATE users SET balance = balance + :p WHERE id = :uid"),
            {"p": int(seller_gain), "uid": int(seller_uid)},
        )

        if buy_qty >= lot_qty:
            await self._s.execute(
                text(
                    """
                    UPDATE market_listings
                    SET status = 'sold'
                    WHERE id = :lid
                    """
                ),
                {"lid": int(lid)},
            )
        else:
            new_qty = int(lot_qty - buy_qty)
            new_price = int(lot_price - price)
            if new_qty < 1:
                new_qty = 1
            if new_price < 0:
                new_price = 0
            await self._s.execute(
                text(
                    """
                    UPDATE market_listings
                    SET qty = :q, price = :p
                    WHERE id = :lid AND status = 'active'
                    """
                ),
                {"lid": int(lid), "q": int(new_qty), "p": int(new_price)},
            )

        await self._s.execute(
            text(
                """
                INSERT INTO character_inventory (character_id, item_id, qty)
                VALUES (:cid, :iid, :qty)
                ON CONFLICT (character_id, item_id)
                DO UPDATE SET qty = character_inventory.qty + EXCLUDED.qty
                """
            ),
            {"cid": int(character_id), "iid": int(item_id), "qty": int(buy_qty)},
        )

        await self._s.commit()
        return PurchaseResult(
            listing_id=lid,
            item_id=item_id,
            item_name=item_name,
            qty=buy_qty,
            price=price,
            fee=fee,
            seller_user_id=seller_uid,
        )
