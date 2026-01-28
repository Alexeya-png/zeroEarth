from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MarketError(Exception):
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
class MarketListingView:
    id: int
    item_name: str
    item_type: str
    qty: int
    price: int


@dataclass(frozen=True)
class MarketListingDetails:
    id: int
    item_id: int
    item_name: str
    item_type: str
    meta_json: dict[str, Any]
    qty: int
    price: int
    seller_tg_id: int
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


class MarketService:
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

    async def active_count(self) -> int:
        r = (
            await self._s.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM market_listings
                    WHERE status = 'active'
                    """
                )
            )
        ).mappings().first()
        return int((r or {}).get("cnt") or 0)

    async def list_active(self, limit: int = 30, offset: int = 0) -> list[MarketListingView]:
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ml.id,
                      ml.qty,
                      ml.price,
                      i.name AS item_name,
                      i.item_type AS item_type
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    WHERE ml.status = 'active'
                    ORDER BY ml.created_at DESC, ml.id DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"lim": int(limit), "off": int(offset)},
            )
        ).mappings().all()

        out: list[MarketListingView] = []
        for r in rows:
            out.append(
                MarketListingView(
                    id=int(r.get("id") or 0),
                    item_name=str(r.get("item_name") or "Предмет"),
                    item_type=str(r.get("item_type") or "misc"),
                    qty=int(r.get("qty") or 1),
                    price=int(r.get("price") or 0),
                )
            )
        return out

    async def get_page(self, page: int, page_size: int = 30) -> MarketPage:
        total = await self.active_count()
        if page_size <= 0:
            page_size = 30

        max_page = 0
        if total > 0:
            max_page = (total - 1) // page_size

        page = max(0, int(page))
        if page > max_page:
            page = max_page

        offset = page * page_size
        listings = await self.list_active(limit=page_size, offset=offset)

        return MarketPage(
            page=page,
            page_size=page_size,
            total=total,
            max_page=max_page,
            listings=listings,
            has_prev=page > 0,
            has_next=page < max_page,
        )

    def render_market_table(self, listings: list[MarketListingView]) -> str:
        if not listings:
            return "<pre>Лотов нет</pre>"

        IDX_W = 2
        ITEM_W = 32
        QTY_W = 4
        PRICE_W = 7
        TYPE_W = 12

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Предмет", ITEM_W),
                _cell("Кол", QTY_W, align="right"),
                _cell("Цена", PRICE_W, align="right"),
                _cell("Тип", TYPE_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * ITEM_W,
                "-" * QTY_W,
                "-" * PRICE_W,
                "-" * TYPE_W,
            ]
        )

        rows: list[str] = [header, sep]
        for idx, l in enumerate(listings, start=1):
            qty = max(1, int(l.qty))
            price = max(0, int(l.price))
            rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(l.item_name, ITEM_W),
                        _cell(qty, QTY_W, align="right"),
                        _cell(price, PRICE_W, align="right"),
                        _cell(l.item_type, TYPE_W),
                    ]
                )
            )

        return "<pre>" + "\n".join(rows) + "</pre>"

    async def market_text(self, *, page: int, page_size: int = 30) -> tuple[str, MarketPage]:
        mp = await self.get_page(page=page, page_size=page_size)

        page_label = f"{mp.page + 1}/{mp.max_page + 1}" if (mp.total > 0) else "1/1"
        parts = [
            "<b>Рынок</b>",
            f"Активные лоты: {mp.total}",
            f"Страница: {page_label}",
            self.render_market_table(mp.listings),
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

        return MarketListingDetails(
            id=int(r.get("id") or 0),
            item_id=int(r.get("item_id") or 0),
            item_name=str(r.get("item_name") or "Предмет"),
            item_type=str(r.get("item_type") or "misc"),
            meta_json=meta_json,
            qty=int(r.get("qty") or 1),
            price=int(r.get("price") or 0),
            seller_tg_id=int(r.get("seller_tg_id") or 0),
            created_at=_fmt_dt(r.get("created_at")),
        )

    def _kv_table(self, rows: list[tuple[str, Any]]) -> str:
        KEY_W = 18
        VAL_W = 24

        header = " | ".join([_cell("Параметр", KEY_W), _cell("Значение", VAL_W)])
        sep = "-+-".join(["-" * KEY_W, "-" * VAL_W])

        out = [header, sep]
        for k, v in rows:
            out.append(" | ".join([_cell(k, KEY_W), _cell(v, VAL_W)]))
        return "<pre>" + "\n".join(out) + "</pre>"

    def _rows_table(self, headers: list[tuple[str, int, str]], rows: list[list[Any]]) -> str:
        head_line = " | ".join([_cell(h, w, align=a) for h, w, a in headers])
        sep = "-+-".join(["-" * w for _, w, _ in headers])
        out = [head_line, sep]
        for r in rows:
            out.append(" | ".join([_cell(v, headers[i][1], align=headers[i][2]) for i, v in enumerate(r)]))
        return "<pre>" + "\n".join(out) + "</pre>"

    def _selected_item_block(self, l: MarketListingDetails) -> str:
        return (
            "<b>Выбранный предмет</b>\n"
            f"<pre>{_esc(l.item_name)} – ID предмета {l.item_id} – ID лота {l.id}</pre>"
        )

    async def _equipment_stats_table(self, item_id: int) -> str | None:
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

        def _f(v: Any) -> str:
            try:
                num = float(v or 0)
            except Exception:
                num = 0.0
            if abs(num - int(num)) < 1e-9:
                return str(int(num))
            return f"{num:.1f}"

        rows = [
            ("Тир", str(r.get("tier") or "D")),
            ("Броня", _n(r.get("armor"))),
            ("Надёжность", _n(r.get("reliability"))),
            ("Точность", f"{_n(r.get('accuracy_bonus')):+}"),
            ("Реакция", f"{_f(r.get('reaction_bonus')):+}"),
            ("Инициатива", f"{_f(r.get('initiative_bonus')):+}"),
            ("Скрытность", f"{_f(r.get('stealth_bonus')):+}"),
            ("Грузоподъём", f"{_f(r.get('carry_capacity_bonus')):+}"),
            ("Анализ лута", f"{_n(r.get('loot_analysis_bonus')):+}"),
            ("Обращение", f"{_n(r.get('item_handling_bonus')):+}"),
        ]

        return self._kv_table(rows)

    async def _ammo_details(self, meta_json: dict[str, Any], item_id: int) -> tuple[str, str] | None:
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

        ammo_rows = [
            ("ID предмета", int(item_id)),
            ("Калибр", str(r.get("caliber_name") or r.get("code") or "")),
            ("Тип", str(r.get("name") or "")),
            ("Урон", int(r.get("damage") or 0)),
            ("Пробитие", int(r.get("armor_penetration") or 0)),
        ]
        ammo_table = self._kv_table(ammo_rows)

        caliber_id = int(r.get("caliber_id") or 0)
        w_rows = (
            await self._s.execute(
                text(
                    """
                    SELECT id, name, category, accuracy, reliability
                    FROM weapons
                    WHERE caliber_id = :cid
                    ORDER BY category, name
                    LIMIT 25
                    """
                ),
                {"cid": caliber_id},
            )
        ).mappings().all()

        headers = [
            ("ID", 5, "right"),
            ("Оружие", 24, "left"),
            ("Кат", 7, "left"),
            ("ТЧН", 4, "right"),
            ("НАД", 4, "right"),
        ]
        w_table = self._rows_table(
            headers,
            [
                [
                    int(w.get("id") or 0),
                    str(w.get("name") or ""),
                    str(w.get("category") or ""),
                    int(w.get("accuracy") or 0),
                    int(w.get("reliability") or 0),
                ]
                for w in w_rows
            ]
            or [["–", "нет", "–", "–", "–"]],
        )

        return (ammo_table, w_table)

    async def _weapon_mod_details(self, item_id: int) -> tuple[str, str] | None:
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
        uniq = r.get("unique_weapon_id")

        mod_rows = [
            ("ID предмета", int(item_id)),
            ("Тип", str(r.get("mod_type") or "")),
            ("Тир", str(r.get("tier") or "D")),
            ("Лимит", int(r.get("slot_limit") or 1)),
            ("Точность", f"{int(r.get('accuracy_bonus') or 0):+}"),
            ("Надёжность", f"{int(r.get('reliability_bonus') or 0):+}"),
            ("Урон", f"{int(r.get('damage_bonus') or 0):+}"),
            ("Пробитие", f"{int(r.get('armor_pen_bonus') or 0):+}"),
            ("Катег", ",".join(cats) if cats else "–"),
            ("Уник", int(uniq) if uniq is not None else "–"),
        ]
        mod_table = self._kv_table(mod_rows)

        if uniq is not None:
            w_rows = (
                await self._s.execute(
                    text(
                        """
                        SELECT id, name, category, accuracy, reliability
                        FROM weapons
                        WHERE id = :wid
                        LIMIT 1
                        """
                    ),
                    {"wid": int(uniq)},
                )
            ).mappings().all()
        elif cats:
            w_rows = (
                await self._s.execute(
                    text(
                        """
                        SELECT id, name, category, accuracy, reliability
                        FROM weapons
                        WHERE category = ANY(:cats)
                        ORDER BY category, name
                        LIMIT 25
                        """
                    ),
                    {"cats": cats},
                )
            ).mappings().all()
        else:
            w_rows = []

        headers = [
            ("ID", 5, "right"),
            ("Оружие", 24, "left"),
            ("Кат", 7, "left"),
            ("ТЧН", 4, "right"),
            ("НАД", 4, "right"),
        ]
        w_table = self._rows_table(
            headers,
            [
                [
                    int(w.get("id") or 0),
                    str(w.get("name") or ""),
                    str(w.get("category") or ""),
                    int(w.get("accuracy") or 0),
                    int(w.get("reliability") or 0),
                ]
                for w in w_rows
            ]
            or [["–", "нет", "–", "–", "–"]],
        )

        return (mod_table, w_table)

    async def listing_details_text(self, listing_id: int) -> str:
        l = await self._get_listing(int(listing_id))
        if not l:
            return "<b>Рынок – подробнее</b>\n\nЛот не найден."

        base_rows = [
            ("ID лота", l.id),
            ("ID предмета", l.item_id),
            ("Предмет", l.item_name),
            ("Кол-во", max(1, int(l.qty))),
            ("Цена", max(0, int(l.price))),
            ("Продавец", l.seller_tg_id if l.seller_tg_id else "–"),
            ("Дата", l.created_at),
        ]

        blocks: list[str] = ["<b>Рынок – подробнее</b>", self._kv_table(base_rows)]

        it = (l.item_type or "").lower()
        kind = str(l.meta_json.get("kind") or "").lower()

        is_equipment = it in {"head", "body", "gloves", "boots"}
        is_ammo = it == "ammo" or kind == "ammo"
        is_mod = it in {"weapon upgrade", "weapon_upgrade"}

        if is_equipment:
            t = await self._equipment_stats_table(l.item_id)
            blocks.append("<b>Бонусы экипировки</b>")
            blocks.append(t if t else "<pre>нет данных</pre>")
            blocks.append(self._selected_item_block(l))
            return "\n".join(blocks).rstrip()

        if is_ammo:
            res = await self._ammo_details(l.meta_json, l.item_id)
            if res:
                ammo_table, weapons_table = res
                blocks.append("<b>Патроны</b>")
                blocks.append(ammo_table)
                blocks.append("<b>Подходит к оружию</b>")
                blocks.append(weapons_table)
            else:
                blocks.append("<pre>Патроны – в разработке</pre>")
            blocks.append(self._selected_item_block(l))
            return "\n".join(blocks).rstrip()

        mod_res: tuple[str, str] | None = None
        if is_mod:
            mod_res = await self._weapon_mod_details(l.item_id)
        else:
            mod_res = await self._weapon_mod_details(l.item_id)
            if mod_res:
                is_mod = True

        if is_mod:
            if mod_res:
                mod_table, weapons_table = mod_res
                blocks.append("<b>Апгрейд оружия</b>")
                blocks.append(mod_table)
                blocks.append("<b>Подходит к оружию</b>")
                blocks.append(weapons_table)
            else:
                blocks.append("<pre>Апгрейд оружия – в разработке</pre>")
            blocks.append(self._selected_item_block(l))
            return "\n".join(blocks).rstrip()

        blocks.append("<pre>В разработке</pre>")
        blocks.append(self._selected_item_block(l))
        return "\n".join(blocks).rstrip()

    async def sellable_inventory(self, tg_id: int, character_id: int, *, limit: int = 30, offset: int = 0) -> tuple[str, list[SellInventoryItemView]]:
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
                    SELECT ci.item_id, ci.qty, i.name, i.item_type
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
                )
            )

        if not items:
            text_out = (
                f"<b>Выставить на рынок</b> – {_esc(str(ch.get('name') or 'Персонаж'))}\n"
                "Нет предметов для выставления."
            )
            return text_out, items

        IDX_W = 2
        ITEM_W = 32
        QTY_W = 4
        TYPE_W = 12

        header = " | ".join(
            [
                _cell("№", IDX_W, align="right"),
                _cell("Предмет", ITEM_W),
                _cell("Кол", QTY_W, align="right"),
                _cell("Тип", TYPE_W),
            ]
        )
        sep = "-+-".join(
            [
                "-" * IDX_W,
                "-" * ITEM_W,
                "-" * QTY_W,
                "-" * TYPE_W,
            ]
        )

        table_rows: list[str] = [header, sep]
        for idx, it in enumerate(items, start=1):
            table_rows.append(
                " | ".join(
                    [
                        _cell(idx, IDX_W, align="right"),
                        _cell(it.name, ITEM_W),
                        _cell(max(1, int(it.qty)), QTY_W, align="right"),
                        _cell(it.item_type, TYPE_W),
                    ]
                )
            )

        shown = min(len(items), limit)
        title = f"<b>Выставить на рынок</b> – {_esc(str(ch.get('name') or 'Персонаж'))}"
        info = f"Доступно: {total_cnt} | Показано: {shown}"
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
            text_out = "\n".join([title, info, "Напиши № предмета из таблицы."]).rstrip()

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
