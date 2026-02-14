from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class UserInfo:
    id: int
    tg_id: int
    account_tier: str
    character_slots: int
    balance: int


@dataclass(frozen=True)
class StashPage:
    text: str
    page: int
    total_pages: int
    total_items: int
    # кнопки для WebApp по текущей странице – (номер в списке, item_id)
    page_links: list[tuple[int, int]]


class StashError(Exception):
    pass


def _miniapp_href(start_param: str) -> str:
    bot_username = (os.getenv("TG_BOT_USERNAME") or "").lstrip("@")
    webapp_name = (os.getenv("TG_WEBAPP_NAME") or "").strip().lstrip("/")

    if not bot_username:
        bot_username = "zeroearth_bot"
    if not webapp_name:
        webapp_name = "stash"

    return f"https://t.me/{bot_username}/{webapp_name}?startapp={start_param}"


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def _fmt_kg(x: float) -> str:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return f"{s} кг"


def _truncate(s: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len == 1:
        return "…"
    return s[: max_len - 1] + "…"


def _ceil_div(a: int, b: int) -> int:
    if b <= 0:
        return 1
    return int(math.ceil(a / b)) if a > 0 else 1


def _link_name(item_id: Any, escaped_name: str) -> str:
    if item_id is None:
        return escaped_name
    try:
        iid = int(item_id)
    except Exception:
        return escaped_name

    href = _miniapp_href(f"i{iid}")
    return f'<a href="{href}">{escaped_name}</a>'


def _fmt_line(item_id: Any, name: Any, weight: Any, loot_type: Any, tier: Any) -> str:
    n = _esc(str(name))
    n = _link_name(item_id, n)
    w = _fmt_kg(_to_float(weight))
    lt = str(loot_type or "common")
    t = str(tier or "").strip()
    t = _esc(t) if t else ""
    if t:
        return f"{n} – {w} – {t} – {lt}"
    return f"{n} – {w} – {lt}"


def _render_inventory_text(rows: list[dict[str, str]], start_index: int) -> str:
    if not rows:
        return "Пусто"

    out: list[str] = []
    idx = start_index
    for r in rows:
        name = r["name"]

        item_id = (r.get("item_id") or "").strip()
        if item_id.isdigit():
            href = _miniapp_href(f"i{item_id}")
            name = f'<a href="{href}">{name}</a>'

        qty = r["qty"]
        weight = r["weight"]
        loot_type = r["rarity"]
        tier = (r.get("tier") or "").strip()
        tier_part = f"{tier} – " if tier else ""
        out.append(f"{idx}. {name} – x{qty} – {weight} – {tier_part}{loot_type}")
        idx += 1

    return "\n".join(out)


class StashService:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def ensure_user(self, tg_id: int) -> UserInfo:
        row = (
            await self._s.execute(
                text(
                    """
                    SELECT id, tg_id, account_tier, character_slots, balance
                    FROM users
                    WHERE tg_id = :tg_id
                    """
                ),
                {"tg_id": tg_id},
            )
        ).mappings().first()

        if row:
            return UserInfo(
                id=int(row["id"]),
                tg_id=int(row["tg_id"]),
                account_tier=str(row["account_tier"]),
                character_slots=int(row["character_slots"]),
                balance=int(row["balance"]),
            )

        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO users (tg_id)
                    VALUES (:tg_id)
                    RETURNING id, tg_id, account_tier, character_slots, balance
                    """
                ),
                {"tg_id": tg_id},
            )
        ).mappings().first()

        await self._s.commit()

        return UserInfo(
            id=int(row["id"]),
            tg_id=int(row["tg_id"]),
            account_tier=str(row["account_tier"]),
            character_slots=int(row["character_slots"]),
            balance=int(row["balance"]),
        )

    async def character_stash_text(self, tg_id: int, character_id: int) -> str:
        page = await self.character_stash_page(tg_id, character_id, page=0, page_size=15)
        return page.text

    async def character_stash_page(
        self,
        tg_id: int,
        character_id: int,
        page: int = 0,
        page_size: int = 15,
    ) -> StashPage:
        user = await self.ensure_user(tg_id)

        ch = (
            await self._s.execute(
                text(
                    """
                    SELECT id, name
                    FROM characters
                    WHERE id = :cid AND user_id = :uid
                    """
                ),
                {"cid": character_id, "uid": user.id},
            )
        ).mappings().first()

        if not ch:
            raise StashError("Персонаж не найден.")

        eq = (
            await self._s.execute(
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
                {"cid": character_id},
            )
        ).mappings().first()

        inv = (
            await self._s.execute(
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
                {"cid": character_id},
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
                    iid = int(v)
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
                total_weight += _to_float(eq.get(k))

        items_rows: list[dict[str, str]] = []
        if inv:
            for row in inv:
                item_id = int(row["item_id"])
                qty = int(row.get("qty") or 1)
                equipped_qty = equipped_counts.get(item_id, 0)
                show_qty = qty - equipped_qty
                if show_qty <= 0:
                    continue

                w_total = _to_float(row.get("weight_each")) * float(show_qty)
                total_weight += w_total

                n = _esc(str(row["name"]))
                if len(n) > 70:
                    n = _truncate(n, 70)

                loot_type = str(row.get("loot_type") or "common")

                tier = str(row.get("tier") or "").strip()
                tier = _esc(tier) if tier else ""

                items_rows.append(
                    {
                        "item_id": str(item_id),
                        "name": n,
                        "qty": str(show_qty),
                        "weight": _fmt_kg(w_total),
                        "rarity": loot_type,
                        "tier": tier,
                    }
                )

        total_items = len(items_rows)
        total_pages = _ceil_div(total_items, page_size)

        if page < 0:
            page = 0
        if page >= total_pages:
            page = total_pages - 1

        start_i = page * page_size
        end_i = min(start_i + page_size, total_items)
        page_rows = items_rows[start_i:end_i]

        page_links: list[tuple[int, int]] = []
        for i, r in enumerate(page_rows, start=start_i + 1):
            item_id_str = (r.get("item_id") or "").strip()
            if item_id_str.isdigit():
                page_links.append((i, int(item_id_str)))

        name = _esc(str(ch["name"] or "Без имени"))

        lines: list[str] = [
            f"<b>Склад</b> #{int(ch['id'])} – {name}",
            f"Общий вес: {_fmt_kg(total_weight)}",
            "",
            "<b>Надето – броня</b>",
        ]

        armor_lines: list[str] = []
        if eq:
            for id_k, n_k, w_k, lt_k, t_k in (
                ("head_item_id", "head_name", "head_weight", "head_loot_type", "head_tier"),
                ("body_item_id", "body_name", "body_weight", "body_loot_type", "body_tier"),
                ("gloves_item_id", "gloves_name", "gloves_weight", "gloves_loot_type", "gloves_tier"),
                ("boots_item_id", "boots_name", "boots_weight", "boots_loot_type", "boots_tier"),
            ):
                n = eq.get(n_k)
                if not n:
                    continue
                armor_lines.append(_fmt_line(eq.get(id_k), n, eq.get(w_k), eq.get(lt_k), eq.get(t_k)))

        lines.extend(armor_lines or ["Пусто"])
        lines += ["", "<b>Надето – оружие</b>"]

        weapon_lines: list[str] = []
        if eq:
            for id_k, n_k, w_k, lt_k, t_k in (
                ("weapon_1_id", "w1_name", "w1_weight", "w1_loot_type", "w1_tier"),
                ("weapon_2_id", "w2_name", "w2_weight", "w2_loot_type", "w2_tier"),
                ("weapon_3_id", "w3_name", "w3_weight", "w3_loot_type", "w3_tier"),
            ):
                n = eq.get(n_k)
                if not n:
                    continue
                weapon_lines.append(_fmt_line(eq.get(id_k), n, eq.get(w_k), eq.get(lt_k), eq.get(t_k)))

        lines.extend(weapon_lines or ["Пусто"])
        lines += ["", "<b>Инвентарь</b>"]

        if total_items <= 0:
            lines.append("Пусто")
        else:
            lines.append(f"Страница {page + 1}/{total_pages} – {start_i + 1}-{end_i} из {total_items}")
            lines.append(_render_inventory_text(page_rows, start_index=start_i + 1))

        out = "\n".join(lines).rstrip()

        if len(out) > 4096 and page_rows:
            cut = len(page_rows)
            while cut > 1 and len(out) > 4096:
                cut -= 1
                tmp_lines = lines[:-1] + [_render_inventory_text(page_rows[:cut], start_index=start_i + 1)]
                out = "\n".join(tmp_lines).rstrip()

            page_links = page_links[:cut]

            if len(out) > 4096:
                out = out[:4090] + "…"
                page_links = page_links[: max(0, len(page_links))]

        return StashPage(
            text=out,
            page=page,
            total_pages=total_pages,
            total_items=total_items,
            page_links=page_links,
        )
