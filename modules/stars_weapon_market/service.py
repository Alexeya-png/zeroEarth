from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.equip.service import EquipService, EquipError
from modules.weapon_upgrades.service import WeaponUpgradeService
import logging
log = logging.getLogger(__name__)


class StarsWeaponMarketError(Exception):
    pass


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


def _clean_cell(v: Any) -> str:
    txt = str(v or "")
    return txt.replace("\n", " ").replace("\r", " ")


def _truncate(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width == 1:
        return s[:1]
    return s[: width - 1] + "…"


def _cell(v: Any, width: int, *, align: str = "left") -> str:
    raw = _truncate(_clean_cell(v), width)
    if align == "right":
        raw = raw.rjust(width)
    else:
        raw = raw.ljust(width)
    return _esc(raw)


@dataclass(frozen=True)
class ListingView:
    id: int
    weapon_name: str
    category: str
    price_stars: int
    tier: str
    is_unique: bool


@dataclass(frozen=True)
class ListingDetails:
    id: int
    weapon_id: int
    weapon_name: str
    category: str
    caliber_code: str
    caliber_name: str
    accuracy: int
    reliability: int
    weight_kg: float
    quality_tier: str
    quality_score: int
    is_unique: bool
    unique_total_bonus: dict[str, int]
    mods_count: int
    price_stars: int
    status: str
    seller_tg_id: int
    created_at: str


@dataclass(frozen=True)
class MarketPage:
    page: int
    page_size: int
    total: int
    max_page: int
    listings: list[ListingView]
    has_prev: bool
    has_next: bool


@dataclass(frozen=True)
class SellableWeaponView:
    weapon_id: int
    name: str
    category: str
    accuracy: int
    reliability: int
    tier: str
    equipped_slot: int | None
    is_unique: bool


@dataclass(frozen=True)
class UserListingView:
    id: int
    weapon_name: str
    category: str
    price_stars: int


class StarsWeaponMarketService:
    CURRENCY = "XTR"
    RESERVE_MINUTES = 5
    BOT_FEE_PERCENT = 0

    def __init__(self, session: AsyncSession):
        self._s = session

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
            raise StarsWeaponMarketError("Не удалось создать пользователя.")
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
            raise StarsWeaponMarketError("Персонаж не найден.")
        return ch

    async def release_expired_reservations(self) -> None:
        try:
            await self._s.execute(
                text(
                    """
                    UPDATE stars_weapon_listings
                    SET status = 'active',
                        reserved_by_user_id = NULL,
                        reserved_until = NULL
                    WHERE status = 'reserved'
                      AND reserved_until IS NOT NULL
                      AND reserved_until < NOW()
                    """
                )
            )
            await self._s.commit()
        except Exception:
            await self._s.rollback()
            return

    async def active_count(self, *, exclude_user_id: int | None = None) -> int:
        if exclude_user_id is None:
            stmt = text(
                """
                SELECT COUNT(*) AS cnt
                FROM stars_weapon_listings
                WHERE status = 'active'
                """
            )
            params: dict[str, Any] = {}
        else:
            stmt = text(
                """
                SELECT COUNT(*) AS cnt
                FROM stars_weapon_listings
                WHERE status = 'active'
                  AND seller_user_id <> :uid
                """
            )
            params = {"uid": int(exclude_user_id)}

        r = (await self._s.execute(stmt, params)).mappings().first()
        return int((r or {}).get("cnt") or 0)

    async def list_active(self, *, limit: int, offset: int, exclude_user_id: int | None = None) -> list[ListingView]:
        sql = """
                    SELECT
                      l.id,
                      l.price_stars,
                      w.name AS weapon_name,
                      w.category AS category,
                      COALESCE(w.quality_tier, 'D') AS tier,
                      EXISTS (SELECT 1 FROM weapon_uniques wu WHERE wu.weapon_id = w.id) AS is_unique
                    FROM stars_weapon_listings l
                    JOIN weapons w ON w.id = l.weapon_id
                    WHERE l.status = 'active'
        """
        params: dict[str, Any] = {
            "lim": int(limit),
            "off": int(offset),
        }
        if exclude_user_id is not None:
            sql += "\n                      AND l.seller_user_id <> :uid"
            params["uid"] = int(exclude_user_id)

        sql += """
                    ORDER BY l.created_at DESC, l.id DESC
                    LIMIT :lim OFFSET :off
                    """

        rows = (await self._s.execute(text(sql), params)).mappings().all()

        out: list[ListingView] = []
        for r in rows:
            out.append(
                ListingView(
                    id=int(r.get("id") or 0),
                    weapon_name=str(r.get("weapon_name") or "Оружие"),
                    category=str(r.get("category") or ""),
                    price_stars=int(r.get("price_stars") or 0),
                    tier=str(r.get("tier") or "D"),
                    is_unique=bool(r.get("is_unique") or False),
                )
            )
        return out

    async def get_page(self, *, page: int, page_size: int, exclude_user_id: int | None = None) -> MarketPage:
        await self.release_expired_reservations()

        total = await self.active_count(exclude_user_id=exclude_user_id)
        if page_size <= 0:
            page_size = 20

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

    def render_market_table(self, listings: list[ListingView]) -> str:
        if not listings:
            return "<pre>Лотов нет</pre>"

        IDX_W = 2
        WPN_W = 28
        PRICE_W = 7
        CAT_W = 7
        TIER_W = 2
        UNI_W = 1

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Оружие", WPN_W),
                _cell("⭐", PRICE_W, align="right"),
                _cell("Кат", CAT_W),
                _cell("T", TIER_W),
                _cell("U", UNI_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * WPN_W,
                "-" * PRICE_W,
                "-" * CAT_W,
                "-" * TIER_W,
                "-" * UNI_W,
            ]
        )

        rows: list[str] = [header, sep]
        for idx, l in enumerate(listings, start=1):
            rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(l.weapon_name, WPN_W),
                        _cell(max(0, int(l.price_stars)), PRICE_W, align="right"),
                        _cell(l.category, CAT_W),
                        _cell(l.tier, TIER_W),
                        _cell("✓" if l.is_unique else "", UNI_W),
                    ]
                )
            )

        return "<pre>" + "\n".join(rows) + "</pre>"

    async def market_text(self, *, page: int, page_size: int, exclude_user_id: int | None = None) -> tuple[str, MarketPage]:
        mp = await self.get_page(page=page, page_size=page_size, exclude_user_id=exclude_user_id)

        page_label = f"{mp.page + 1}/{mp.max_page + 1}" if (mp.total > 0) else "1/1"
        parts = [
            "<b>Рынок оружия – Stars</b>",
            f"Активные лоты: {mp.total}",
            f"Страница: {page_label}",
            self.render_market_table(mp.listings),
            "<pre>Выбери номер лота и отправь в чат</pre>",
        ]
        return ("\n".join(parts).rstrip(), mp)

    async def get_listing_details(self, listing_id: int) -> Optional[ListingDetails]:
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      l.id,
                      l.weapon_id,
                      l.price_stars,
                      l.status,
                      l.created_at,
                      w.name AS weapon_name,
                      w.category AS category,
                      w.accuracy,
                      w.reliability,
                      COALESCE(w.weight_kg, 0) AS weight_kg,
                      COALESCE(w.quality_tier, 'D') AS quality_tier,
                      COALESCE(w.quality_score, 0) AS quality_score,
                      c.code AS caliber_code,
                      COALESCE(c.name, '') AS caliber_name,
                      u.tg_id AS seller_tg_id
                    FROM stars_weapon_listings l
                    JOIN weapons w ON w.id = l.weapon_id
                    JOIN calibers c ON c.id = w.caliber_id
                    JOIN users u ON u.id = l.seller_user_id
                    WHERE l.id = :lid
                    LIMIT 1
                    """
                ),
                {"lid": int(listing_id)},
            )
        ).mappings().first()

        if not r:
            return None

        weapon_id = int(r.get("weapon_id") or 0)
        wup = WeaponUpgradeService(self._s)
        uniq = await wup.get_unique_info(weapon_id)
        is_unique = uniq is not None
        tb = uniq.total_bonus if uniq else {}
        mods_count = len(uniq.mods) if uniq else 0

        return ListingDetails(
            id=int(r.get("id") or 0),
            weapon_id=weapon_id,
            weapon_name=str(r.get("weapon_name") or "Оружие"),
            category=str(r.get("category") or ""),
            caliber_code=str(r.get("caliber_code") or ""),
            caliber_name=str(r.get("caliber_name") or ""),
            accuracy=int(r.get("accuracy") or 0),
            reliability=int(r.get("reliability") or 0),
            weight_kg=float(r.get("weight_kg") or 0),
            quality_tier=str(r.get("quality_tier") or "D"),
            quality_score=int(r.get("quality_score") or 0),
            is_unique=is_unique,
            unique_total_bonus={
                "accuracy_bonus": int(tb.get("accuracy_bonus", 0) or 0),
                "reliability_bonus": int(tb.get("reliability_bonus", 0) or 0),
                "damage_bonus": int(tb.get("damage_bonus", 0) or 0),
                "armor_pen_bonus": int(tb.get("armor_pen_bonus", 0) or 0),
            },
            mods_count=int(mods_count),
            price_stars=int(r.get("price_stars") or 0),
            status=str(r.get("status") or "active"),
            seller_tg_id=int(r.get("seller_tg_id") or 0),
            created_at=_fmt_dt(r.get("created_at")),
        )

    def listing_details_text(self, d: ListingDetails) -> str:
        bonus = d.unique_total_bonus or {}
        b_lines = []
        if d.is_unique:
            b_lines.append(f"Уникальное: <b>да</b> – модов: <b>{d.mods_count}</b>")
            if any(int(v) != 0 for v in bonus.values()):
                b_lines.append(
                    "Бонусы: "
                    f"Точн {bonus.get('accuracy_bonus', 0):+d}, "
                    f"Над {bonus.get('reliability_bonus', 0):+d}, "
                    f"Урон {bonus.get('damage_bonus', 0):+d}, "
                    f"Проб {bonus.get('armor_pen_bonus', 0):+d}"
                )
        else:
            b_lines.append("Уникальное: <b>нет</b>")

        lines = [
            "<b>Лот оружия – Stars</b>",
            f"<pre>{_esc(d.weapon_name)} – ID оружия {d.weapon_id} – ID лота {d.id}</pre>",
            f"Цена: <b>⭐{max(0, int(d.price_stars))}</b>",
            f"Категория: <b>{_esc(d.category)}</b>",
            f"Калибр: <b>{_esc(d.caliber_name or d.caliber_code)}</b>",
            f"Точность: <b>{int(d.accuracy)}</b>",
            f"Надёжность: <b>{int(d.reliability)}</b>",
            f"Тир: <b>{_esc(d.quality_tier)}</b> – score: <b>{int(d.quality_score)}</b>",
            f"Вес: <b>{d.weight_kg:.3f}</b> кг",
            "",
            "\n".join(b_lines).rstrip(),
            "",
            f"Создан: <b>{_esc(d.created_at)}</b>",
        ]
        return "\n".join([x for x in lines if x is not None]).rstrip()

    async def list_sellable_weapons(self, tg_id: int, character_id: int, limit: int, offset: int) -> list[SellableWeaponView]:
        await self._get_character_owned(tg_id, character_id)

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ci.item_id AS weapon_id,
                      w.name,
                      w.category,
                      w.accuracy,
                      w.reliability,
                      COALESCE(w.quality_tier, 'D') AS tier,
                      CASE
                        WHEN e.weapon_1_id = ci.item_id THEN 1
                        WHEN e.weapon_2_id = ci.item_id THEN 2
                        WHEN e.weapon_3_id = ci.item_id THEN 3
                        ELSE NULL
                      END AS equipped_slot,
                      EXISTS (SELECT 1 FROM weapon_uniques wu WHERE wu.weapon_id = ci.item_id) AS is_unique
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    LEFT JOIN equipment e ON e.character_id = ci.character_id
                    WHERE ci.character_id = :cid AND ci.qty > 0
                    ORDER BY w.quality_tier DESC, w.quality_score DESC, w.id DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"cid": int(character_id), "lim": int(limit), "off": int(offset)},
            )
        ).mappings().all()

        out: list[SellableWeaponView] = []
        for r in rows:
            slot = r.get("equipped_slot")
            out.append(
                SellableWeaponView(
                    weapon_id=int(r.get("weapon_id") or 0),
                    name=str(r.get("name") or "Оружие"),
                    category=str(r.get("category") or ""),
                    accuracy=int(r.get("accuracy") or 0),
                    reliability=int(r.get("reliability") or 0),
                    tier=str(r.get("tier") or "D"),
                    equipped_slot=int(slot) if slot is not None else None,
                    is_unique=bool(r.get("is_unique") or False),
                )
            )
        return out

    def render_sellable_weapons_table(self, weapons: list[SellableWeaponView]) -> str:
        if not weapons:
            return "<pre>Оружия нет</pre>"

        IDX_W = 2
        WPN_W = 26
        CAT_W = 7
        TIER_W = 2
        EQ_W = 2
        UNI_W = 1

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Оружие", WPN_W),
                _cell("Кат", CAT_W),
                _cell("T", TIER_W),
                _cell("E", EQ_W),
                _cell("U", UNI_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * WPN_W,
                "-" * CAT_W,
                "-" * TIER_W,
                "-" * EQ_W,
                "-" * UNI_W,
            ]
        )

        rows: list[str] = [header, sep]
        for idx, w in enumerate(weapons, start=1):
            eq = str(w.equipped_slot) if w.equipped_slot else ""
            rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(w.name, WPN_W),
                        _cell(w.category, CAT_W),
                        _cell(w.tier, TIER_W),
                        _cell(eq, EQ_W, align="right"),
                        _cell("✓" if w.is_unique else "", UNI_W),
                    ]
                )
            )

        return "<pre>" + "\n".join(rows) + "</pre>"

    async def create_listing(self, tg_id: int, character_id: int, weapon_id: int, price_stars: int) -> int:
        if int(price_stars) <= 0:
            raise StarsWeaponMarketError("Цена должна быть больше 0.")

        uid = await self._ensure_user_id(tg_id)
        await self._get_character_owned(tg_id, character_id)

        exists = (
            await self._s.execute(
                text(
                    """
                    SELECT 1
                    FROM stars_weapon_listings
                    WHERE weapon_id = :wid AND status IN ('active', 'reserved')
                    LIMIT 1
                    """
                ),
                {"wid": int(weapon_id)},
            )
        ).first()
        if exists:
            raise StarsWeaponMarketError("Это оружие уже выставлено на продажу.")

        ok = (
            await self._s.execute(
                text(
                    """
                    SELECT ci.qty
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    WHERE ci.character_id = :cid AND ci.item_id = :wid AND ci.qty > 0
                    """
                ),
                {"cid": int(character_id), "wid": int(weapon_id)},
            )
        ).first()
        if not ok:
            raise StarsWeaponMarketError("Оружие не найдено на складе.")

        equipped = (
            await self._s.execute(
                text(
                    """
                    SELECT weapon_1_id, weapon_2_id, weapon_3_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).first()

        equipped_slot: int | None = None
        if equipped:
            w1, w2, w3 = equipped
            if w1 is not None and int(w1) == int(weapon_id):
                equipped_slot = 1
            elif w2 is not None and int(w2) == int(weapon_id):
                equipped_slot = 2
            elif w3 is not None and int(w3) == int(weapon_id):
                equipped_slot = 3

        if equipped_slot is not None:
            eq = EquipService(self._s)
            try:
                await eq.ammo_clear(tg_id, character_id, equipped_slot)
                await eq.unequip_weapon(tg_id, character_id, equipped_slot)
            except EquipError:
                raise StarsWeaponMarketError("Не удалось снять оружие с бойца.")

        try:
            row = (
                await self._s.execute(
                    text(
                        """
                        SELECT qty
                        FROM character_inventory
                        WHERE character_id = :cid AND item_id = :wid
                        """
                    ),
                    {"cid": int(character_id), "wid": int(weapon_id)},
                )
            ).first()

            have = int(row[0]) if row and row[0] is not None else 0
            if have <= 0:
                raise StarsWeaponMarketError("Оружие не найдено на складе.")

            if have > 1:
                await self._s.execute(
                    text(
                        """
                        UPDATE character_inventory
                        SET qty = qty - 1
                        WHERE character_id = :cid AND item_id = :wid
                        """
                    ),
                    {"cid": int(character_id), "wid": int(weapon_id)},
                )
            else:
                await self._s.execute(
                    text(
                        """
                        DELETE FROM character_inventory
                        WHERE character_id = :cid AND item_id = :wid
                        """
                    ),
                    {"cid": int(character_id), "wid": int(weapon_id)},
                )

            r2 = (
                await self._s.execute(
                    text(
                        """
                        INSERT INTO stars_weapon_listings (
                          seller_user_id, seller_character_id, weapon_id, price_stars, status
                        )
                        VALUES (:uid, :cid, :wid, :p, 'active')
                        RETURNING id
                        """
                    ),
                    {"uid": int(uid), "cid": int(character_id), "wid": int(weapon_id), "p": int(price_stars)},
                )
            ).first()

            if not r2:
                raise StarsWeaponMarketError("Не удалось создать лот.")
            listing_id = int(r2[0])

            await self._s.commit()
            return listing_id
        except Exception:
            await self._s.rollback()
            raise

    async def list_user_active_listings(self, tg_id: int) -> list[UserListingView]:
        uid = await self._ensure_user_id(tg_id)
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      l.id,
                      l.price_stars,
                      w.name AS weapon_name,
                      w.category AS category
                    FROM stars_weapon_listings l
                    JOIN weapons w ON w.id = l.weapon_id
                    WHERE l.seller_user_id = :uid AND l.status = 'active'
                    ORDER BY l.created_at DESC, l.id DESC
                    """
                ),
                {"uid": int(uid)},
            )
        ).mappings().all()

        out: list[UserListingView] = []
        for r in rows:
            out.append(
                UserListingView(
                    id=int(r.get("id") or 0),
                    weapon_name=str(r.get("weapon_name") or "Оружие"),
                    category=str(r.get("category") or ""),
                    price_stars=int(r.get("price_stars") or 0),
                )
            )
        return out

    def render_user_listings_table(self, listings: list[UserListingView]) -> str:
        if not listings:
            return "<pre>Активных лотов нет</pre>"

        IDX_W = 2
        WPN_W = 28
        PRICE_W = 7
        CAT_W = 7

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Оружие", WPN_W),
                _cell("⭐", PRICE_W, align="right"),
                _cell("Кат", CAT_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * WPN_W,
                "-" * PRICE_W,
                "-" * CAT_W,
            ]
        )

        rows: list[str] = [header, sep]
        for idx, l in enumerate(listings, start=1):
            rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(l.weapon_name, WPN_W),
                        _cell(max(0, int(l.price_stars)), PRICE_W, align="right"),
                        _cell(l.category, CAT_W),
                    ]
                )
            )

        return "<pre>" + "\n".join(rows) + "</pre>"

    async def withdraw_listing(self, tg_id: int, listing_id: int) -> None:
        uid = await self._ensure_user_id(tg_id)

        try:
            row = (
                await self._s.execute(
                    text(
                        """
                        UPDATE stars_weapon_listings
                        SET status = 'canceled'
                        WHERE id = :lid AND seller_user_id = :uid AND status = 'active'
                        RETURNING weapon_id, seller_character_id
                        """
                    ),
                    {"lid": int(listing_id), "uid": int(uid)},
                )
            ).first()

            if not row:
                raise StarsWeaponMarketError("Лот не найден или уже недоступен.")

            weapon_id = int(row[0])
            character_id = int(row[1])

            await self._s.execute(
                text(
                    """
                    INSERT INTO character_inventory (character_id, item_id, qty)
                    VALUES (:cid, :iid, 1)
                    ON CONFLICT (character_id, item_id)
                    DO UPDATE SET qty = character_inventory.qty + 1
                    """
                ),
                {"cid": int(character_id), "iid": int(weapon_id)},
            )

            await self._s.commit()
        except StarsWeaponMarketError:
            await self._s.rollback()
            raise
        except Exception:
            await self._s.rollback()
            raise StarsWeaponMarketError("Не удалось снять лот.")

    async def create_order_and_reserve(self, buyer_tg_id: int, listing_id: int, buyer_character_id: int) -> tuple[str, int, str]:
        await self.release_expired_reservations()

        buyer_uid = await self._ensure_user_id(buyer_tg_id)

        ch = (
            await self._s.execute(
                text(
                    """
                    SELECT 1
                    FROM characters
                    WHERE id = :cid AND user_id = :uid
                    """
                ),
                {"cid": int(buyer_character_id), "uid": int(buyer_uid)},
            )
        ).first()
        if not ch:
            raise StarsWeaponMarketError("Персонаж для получения не найден.")

        listing = (
            await self._s.execute(
                text(
                    """
                    SELECT l.id, l.price_stars, l.seller_user_id, w.name AS weapon_name
                    FROM stars_weapon_listings l
                    JOIN weapons w ON w.id = l.weapon_id
                    WHERE l.id = :lid
                    LIMIT 1
                    """
                ),
                {"lid": int(listing_id)},
            )
        ).mappings().first()

        if not listing:
            raise StarsWeaponMarketError("Лот не найден.")

        if int(listing.get("seller_user_id") or 0) == int(buyer_uid):
            raise StarsWeaponMarketError("Нельзя купить свой лот.")

        price_stars = int(listing.get("price_stars") or 0)
        weapon_name = str(listing.get("weapon_name") or "Оружие")
        if price_stars <= 0:
            raise StarsWeaponMarketError("Некорректная цена.")

        try:
            reserved = (
                await self._s.execute(
                    text(
                        """
                        UPDATE stars_weapon_listings
                        SET status = 'reserved',
                            reserved_by_user_id = :buid,
                            reserved_until = NOW() + (:mins * INTERVAL '1 minute')
                        WHERE id = :lid AND status = 'active'
                        RETURNING id
                        """
                    ),
                    {"lid": int(listing_id), "buid": int(buyer_uid), "mins": int(self.RESERVE_MINUTES)},
                )
            ).first()

            if not reserved:
                raise StarsWeaponMarketError("Лот уже занят или недоступен.")

            token = secrets.token_urlsafe(8)
            payload = f"wstars:{int(listing_id)}:{int(buyer_character_id)}:{token}"

            await self._s.execute(
                text(
                    """
                    INSERT INTO stars_orders (
                      listing_id, buyer_user_id, buyer_tg_id, payload, amount_stars, currency, status
                    )
                    VALUES (:lid, :buid, :btg, :pl, :amt, :cur, 'pending')
                    """
                ),
                {
                    "lid": int(listing_id),
                    "buid": int(buyer_uid),
                    "btg": int(buyer_tg_id),
                    "pl": payload,
                    "amt": int(price_stars),
                    "cur": self.CURRENCY,
                },
            )

            await self._s.commit()
            return payload, int(price_stars), weapon_name
        except StarsWeaponMarketError:
            await self._s.rollback()
            raise
        except Exception as e:
            await self._s.rollback()
            log.exception(
                "wstars create_order_and_reserve failed – buyer_tg_id=%s listing_id=%s buyer_character_id=%s",
                buyer_tg_id, listing_id, buyer_character_id
            )
            raise StarsWeaponMarketError(f"Не удалось создать платёж – {e.__class__.__name__}")

    async def validate_pre_checkout(self, tg_id: int, payload: str, total_amount: int, currency: str) -> tuple[bool, str]:
        if not payload.startswith("wstars:"):
            return False, "Некорректный платёж."

        row = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      o.id,
                      o.status,
                      o.amount_stars,
                      o.currency,
                      o.buyer_tg_id,
                      o.buyer_user_id,
                      o.listing_id,
                      l.status AS listing_status,
                      l.reserved_by_user_id,
                      l.reserved_until
                    FROM stars_orders o
                    JOIN stars_weapon_listings l ON l.id = o.listing_id
                    WHERE o.payload = :pl
                    LIMIT 1
                    """
                ),
                {"pl": payload},
            )
        ).mappings().first()

        if not row:
            return False, "Заказ не найден."

        if int(row.get("buyer_tg_id") or 0) != int(tg_id):
            return False, "Покупатель не совпадает."

        if str(row.get("currency") or "") != str(currency or ""):
            return False, "Некорректная валюта."

        if int(row.get("amount_stars") or 0) != int(total_amount):
            return False, "Некорректная сумма."

        listing_status = str(row.get("listing_status") or "")
        reserved_by = row.get("reserved_by_user_id")
        reserved_until = row.get("reserved_until")

        if listing_status != "reserved":
            return False, "Лот не зарезервирован."

        if reserved_by is None or int(reserved_by) != int(row.get("buyer_user_id") or 0):
            return False, "Лот зарезервирован другим покупателем."

        if reserved_until is None:
            return False, "Резерв истёк."

        try:
            if isinstance(reserved_until, datetime):
                if reserved_until < datetime.now(timezone.utc):
                    return False, "Резерв истёк."
        except Exception:
            return False, "Резерв истёк."

        try:
            if str(row.get("status") or "") == "pending":
                await self._s.execute(
                    text("UPDATE stars_orders SET status = 'prechecked' WHERE id = :id AND status = 'pending'"),
                    {"id": int(row.get("id") or 0)},
                )
                await self._s.commit()
        except Exception:
            await self._s.rollback()

        return True, ""

    async def finalize_payment(self, payload: str, telegram_charge_id: str, provider_charge_id: str | None) -> tuple[str, int]:
        if not payload.startswith("wstars:"):
            raise StarsWeaponMarketError("Некорректный платёж.")

        parts = payload.split(":")
        if len(parts) < 4:
            raise StarsWeaponMarketError("Некорректный платёж.")

        listing_id = int(parts[1])
        buyer_character_id = int(parts[2])

        try:
            row = (
                await self._s.execute(
                    text(
                        """
                        SELECT
                          o.id,
                          o.status,
                          o.buyer_user_id,
                          o.buyer_tg_id,
                          o.amount_stars,
                          l.weapon_id,
                          l.seller_user_id,
                          l.status AS listing_status,
                          l.reserved_by_user_id
                        FROM stars_orders o
                        JOIN stars_weapon_listings l ON l.id = o.listing_id
                        WHERE o.payload = :pl
                        LIMIT 1
                        """
                    ),
                    {"pl": payload},
                )
            ).mappings().first()

            if not row:
                raise StarsWeaponMarketError("Заказ не найден.")

            if str(row.get("status") or "") == "delivered":
                return "Покупка уже обработана.", int(row.get("amount_stars") or 0)

            buyer_uid = int(row.get("buyer_user_id") or 0)
            amount_gross = int(row.get("amount_stars") or 0)
            seller_uid = int(row.get("seller_user_id") or 0)

            ok_ch = (
                await self._s.execute(
                    text("SELECT 1 FROM characters WHERE id = :cid AND user_id = :uid"),
                    {"cid": int(buyer_character_id), "uid": int(buyer_uid)},
                )
            ).first()
            if not ok_ch:
                raise StarsWeaponMarketError("Персонаж для получения не найден.")

            if str(row.get("listing_status") or "") != "reserved":
                raise StarsWeaponMarketError("Лот не в резерве.")

            rbu = row.get("reserved_by_user_id")
            if rbu is None or int(rbu) != int(buyer_uid):
                raise StarsWeaponMarketError("Лот зарезервирован другим покупателем.")

            bot_fee = 0
            seller_net = amount_gross

            upd = (
                await self._s.execute(
                    text(
                        """
                        UPDATE stars_weapon_listings
                        SET status = 'sold',
                            buyer_user_id = :buid,
                            sold_at = NOW(),
                            reserved_by_user_id = NULL,
                            reserved_until = NULL
                        WHERE id = :lid AND status = 'reserved'
                        RETURNING weapon_id
                        """
                    ),
                    {"lid": int(listing_id), "buid": int(buyer_uid)},
                )
            ).first()
            if not upd:
                raise StarsWeaponMarketError("Лот уже недоступен.")

            weapon_id = int(upd[0])

            await self._s.execute(
                text(
                    """
                    INSERT INTO character_inventory (character_id, item_id, qty)
                    VALUES (:cid, :iid, 1)
                    ON CONFLICT (character_id, item_id)
                    DO UPDATE SET qty = character_inventory.qty + 1
                    """
                ),
                {"cid": int(buyer_character_id), "iid": int(weapon_id)},
            )

            await self._s.execute(
                text(
                    """
                    UPDATE stars_orders
                    SET status = 'delivered',
                        telegram_payment_charge_id = :tch,
                        paid_at = NOW(),
                        delivered_at = NOW()
                    WHERE id = :oid
                    """
                ),
                {"oid": int(row.get("id") or 0), "tch": str(telegram_charge_id or "")},
            )

            await self._s.execute(
                text(
                    """
                    INSERT INTO stars_user_balance (user_id, available_stars)
                    VALUES (:uid, :amt)
                    ON CONFLICT (user_id)
                    DO UPDATE SET available_stars = stars_user_balance.available_stars + EXCLUDED.available_stars,
                                  updated_at = NOW()
                    """
                ),
                {"uid": int(seller_uid), "amt": int(seller_net)},
            )

            await self._s.execute(
                text(
                    """
                    INSERT INTO stars_ledger_entries (user_id, order_id, listing_id, delta_stars, kind)
                    VALUES (:uid, :oid, :lid, :delta, 'sale_available')
                    """
                ),
                {
                    "uid": int(seller_uid),
                    "oid": int(row.get("id") or 0),
                    "lid": int(listing_id),
                    "delta": int(seller_net),
                },
            )

            if bot_fee > 0:
                await self._s.execute(
                    text(
                        """
                        INSERT INTO stars_ledger_entries (user_id, order_id, listing_id, delta_stars, kind)
                        VALUES (:uid, :oid, :lid, :delta, 'sale_fee')
                        """
                    ),
                    {"uid": int(seller_uid), "oid": int(row.get("id") or 0), "lid": int(listing_id),
                     "delta": int(-bot_fee)},
                )

            await self._s.commit()
            return "Покупка завершена. Оружие добавлено на склад персонажа.", int(amount_gross)
        except StarsWeaponMarketError:
            await self._s.rollback()
            raise
        except Exception:
            await self._s.rollback()
            raise StarsWeaponMarketError("Не удалось выдать оружие.")
