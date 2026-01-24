from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class UserInfo:
    id: int
    tg_id: int
    account_tier: str
    character_slots: int
    balance: int


class StashError(Exception):
    pass


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
    # 0.000–9999.999 formatting; remove trailing zeros
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return f"{s} кг"


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
                      ih.name AS head_name, ih.weight AS head_weight, COALESCE(ih.loot_type,'common') AS head_loot_type,
                      ib.name AS body_name, ib.weight AS body_weight, COALESCE(ib.loot_type,'common') AS body_loot_type,
                      ig.name AS gloves_name, ig.weight AS gloves_weight, COALESCE(ig.loot_type,'common') AS gloves_loot_type,
                      it.name AS boots_name, it.weight AS boots_weight, COALESCE(it.loot_type,'common') AS boots_loot_type,
                      w1.name AS w1_name, w1.weight_kg AS w1_weight,
                      w2.name AS w2_name, w2.weight_kg AS w2_weight,
                      w3.name AS w3_name, w3.weight_kg AS w3_weight
                    FROM equipment e
                    LEFT JOIN items ih ON ih.id = e.head_item_id
                    LEFT JOIN items ib ON ib.id = e.body_item_id
                    LEFT JOIN items ig ON ig.id = e.gloves_item_id
                    LEFT JOIN items it ON it.id = e.boots_item_id
                    LEFT JOIN weapons w1 ON w1.id = e.weapon_1_id
                    LEFT JOIN weapons w2 ON w2.id = e.weapon_2_id
                    LEFT JOIN weapons w3 ON w3.id = e.weapon_3_id
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
                    SELECT i.name, i.weight, COALESCE(i.loot_type, 'common') AS loot_type, ci.qty
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    WHERE ci.character_id = :cid
                    ORDER BY i.name
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().all()

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

        for row in inv:
            total_weight += _to_float(row.get("weight")) * float(row.get("qty") or 1)

        name = _esc(str(ch["name"] or "Без имени"))

        lines: list[str] = [
            f"<b>Склад</b> #{int(ch['id'])} – {name}",
            f"Общий вес: {_fmt_kg(total_weight)}",
            "",
            "<b>Надето – броня</b>",
        ]

        armor_lines: list[str] = []
        if eq:
            for k_name, k_w, k_t in (
                ("head_name", "head_weight", "head_loot_type"),
                ("body_name", "body_weight", "body_loot_type"),
                ("gloves_name", "gloves_weight", "gloves_loot_type"),
                ("boots_name", "boots_weight", "boots_loot_type"),
            ):
                n = eq.get(k_name)
                if not n:
                    continue
                armor_lines.append(
                    f"{_esc(str(n))} – {_fmt_kg(_to_float(eq.get(k_w)))} – {str(eq.get(k_t) or 'common')}"
                )

        lines.extend(armor_lines or ["Пусто"])

        lines += ["", "<b>Надето – оружие</b>"]

        weapon_lines: list[str] = []
        if eq:
            for k_name, k_w in (
                ("w1_name", "w1_weight"),
                ("w2_name", "w2_weight"),
                ("w3_name", "w3_weight"),
            ):
                n = eq.get(k_name)
                if not n:
                    continue
                weapon_lines.append(
                    f"{_esc(str(n))} – {_fmt_kg(_to_float(eq.get(k_w)))} – common"
                )

        lines.extend(weapon_lines or ["Пусто"])

        lines += ["", "<b>Инвентарь</b>"]

        if inv:
            for row in inv:
                n = _esc(str(row["name"]))
                qty = int(row.get("qty") or 1)
                w = _to_float(row.get("weight")) * float(qty)
                loot_type = str(row.get("loot_type") or "common")
                if qty > 1:
                    lines.append(f"{n} ×{qty} – {_fmt_kg(w)} – {loot_type}")
                else:
                    lines.append(f"{n} – {_fmt_kg(w)} – {loot_type}")
        else:
            lines.append("Пусто")

        return "\n".join(lines).rstrip()
