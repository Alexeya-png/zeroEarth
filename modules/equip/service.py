from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class EquipError(Exception):
    pass


def _esc(s: object) -> str:
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class EquipmentView:
    character_id: int
    character_name: str
    head: str
    body: str
    gloves: str
    boots: str
    w1: str
    w2: str
    w3: str
    head_has_item: bool
    body_has_item: bool
    gloves_has_item: bool
    boots_has_item: bool
    weapons: dict[int, str]


SLOT_TO_COL = {
    "head": "head_item_id",
    "body": "body_item_id",
    "gloves": "gloves_item_id",
    "boots": "boots_item_id",
}


SLOT_TITLE = {
    "head": "Шлем",
    "body": "Торс",
    "gloves": "Перчатки",
    "boots": "Ботинки",
}


class EquipService:
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
            raise EquipError("Не удалось создать пользователя.")
        return int(row[0])

    async def _get_character(self, tg_id: int, character_id: int) -> Mapping[str, Any]:
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
            raise EquipError("Персонаж не найден.")
        return ch

    async def _ensure_equipment_row(self, character_id: int) -> None:
        row = (
            await self._s.execute(
                text("SELECT 1 FROM equipment WHERE character_id = :cid"),
                {"cid": int(character_id)},
            )
        ).first()
        if row:
            return
        await self._s.execute(
            text("INSERT INTO equipment (character_id) VALUES (:cid)"),
            {"cid": int(character_id)},
        )
        await self._s.commit()

    async def equipment_view(self, tg_id: int, character_id: int) -> EquipmentView:
        ch = await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        eq = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ih.name AS head_name, COALESCE(sh.tier,'') AS head_tier, ih.id AS head_id,
                      ib.name AS body_name, COALESCE(sb.tier,'') AS body_tier, ib.id AS body_id,
                      ig.name AS gloves_name, COALESCE(sg.tier,'') AS gloves_tier, ig.id AS gloves_id,
                      it.name AS boots_name, COALESCE(st.tier,'') AS boots_tier, it.id AS boots_id,
                      w1.name AS w1_name,
                      w2.name AS w2_name,
                      w3.name AS w3_name
                    FROM equipment e
                    LEFT JOIN items ih ON ih.id = e.head_item_id
                    LEFT JOIN item_equipment_stats sh ON sh.item_id = e.head_item_id
                    LEFT JOIN items ib ON ib.id = e.body_item_id
                    LEFT JOIN item_equipment_stats sb ON sb.item_id = e.body_item_id
                    LEFT JOIN items ig ON ig.id = e.gloves_item_id
                    LEFT JOIN item_equipment_stats sg ON sg.item_id = e.gloves_item_id
                    LEFT JOIN items it ON it.id = e.boots_item_id
                    LEFT JOIN item_equipment_stats st ON st.item_id = e.boots_item_id
                    LEFT JOIN weapons w1 ON w1.id = e.weapon_1_id
                    LEFT JOIN weapons w2 ON w2.id = e.weapon_2_id
                    LEFT JOIN weapons w3 ON w3.id = e.weapon_3_id
                    WHERE e.character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).mappings().first()

        def _fmt_armor(name: object, tier: object) -> str:
            if not name:
                return "Пусто"
            t = str(tier or "").strip()
            if t:
                return f"{_esc(name)} – {t}"
            return _esc(name)

        def _fmt_weapon(name: object) -> str:
            if not name:
                return "Пусто"
            return _esc(name)

        head = _fmt_armor(eq.get("head_name") if eq else None, eq.get("head_tier") if eq else None)
        body = _fmt_armor(eq.get("body_name") if eq else None, eq.get("body_tier") if eq else None)
        gloves = _fmt_armor(eq.get("gloves_name") if eq else None, eq.get("gloves_tier") if eq else None)
        boots = _fmt_armor(eq.get("boots_name") if eq else None, eq.get("boots_tier") if eq else None)
        w1 = _fmt_weapon(eq.get("w1_name") if eq else None)
        w2 = _fmt_weapon(eq.get("w2_name") if eq else None)
        w3 = _fmt_weapon(eq.get("w3_name") if eq else None)

        weapons = {
            1: str(eq.get("w1_name") or "") if eq else "",
            2: str(eq.get("w2_name") or "") if eq else "",
            3: str(eq.get("w3_name") or "") if eq else "",
        }

        return EquipmentView(
            character_id=int(ch["id"]),
            character_name=str(ch.get("name") or "Без имени"),
            head=head,
            body=body,
            gloves=gloves,
            boots=boots,
            w1=w1,
            w2=w2,
            w3=w3,
            head_has_item=bool(eq and eq.get("head_id")),
            body_has_item=bool(eq and eq.get("body_id")),
            gloves_has_item=bool(eq and eq.get("gloves_id")),
            boots_has_item=bool(eq and eq.get("boots_id")),
            weapons=weapons,
        )

    def equip_text(self, view: EquipmentView) -> str:
        name = _esc(view.character_name)
        lines = [
            "<b>Снарядить бойца</b>",
            f"Персонаж #{view.character_id} – {name}",
            "",
            "<b>Броня</b>",
            f"Шлем: {view.head}",
            f"Торс: {view.body}",
            f"Перчатки: {view.gloves}",
            f"Ботинки: {view.boots}",
            "",
            "<b>Оружие</b>",
            f"1: {view.w1}",
            f"2: {view.w2}",
            f"3: {view.w3}",
        ]
        return "\n".join(lines).rstrip()

    async def list_armor_inventory(
        self,
        tg_id: int,
        character_id: int,
        slot_key: str,
        page: int,
        page_size: int,
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        await self._get_character(tg_id, character_id)
        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        total_row = (
            await self._s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    WHERE ci.character_id = :cid AND i.item_type = :t
                    """
                ),
                {"cid": int(character_id), "t": str(slot_key)},
            )
        ).first()
        total = int(total_row[0] or 0) if total_row else 0

        page = max(0, int(page))
        max_page = (max(total - 1, 0) // int(page_size)) if total > 0 else 0
        if page > max_page:
            page = max_page

        off = page * int(page_size)

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      i.id,
                      i.name,
                      ci.qty,
                      COALESCE(s.tier,'') AS tier,
                      COALESCE(s.armor,0) AS armor,
                      COALESCE(s.reliability,0) AS reliability
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    LEFT JOIN item_equipment_stats s ON s.item_id = i.id
                    WHERE ci.character_id = :cid AND i.item_type = :t
                    ORDER BY tier DESC, i.name ASC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {
                    "cid": int(character_id),
                    "t": str(slot_key),
                    "lim": int(page_size),
                    "off": int(off),
                },
            )
        ).mappings().all()

        return list(rows), total, page

    async def equip_armor_from_inventory(self, tg_id: int, character_id: int, slot_key: str, item_id: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        inv = (
            await self._s.execute(
                text(
                    """
                    SELECT ci.qty, i.item_type
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    WHERE ci.character_id = :cid AND ci.item_id = :iid
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id)},
            )
        ).mappings().first()

        if not inv:
            raise EquipError("Этого предмета нет на складе.")
        if str(inv.get("item_type") or "") != slot_key:
            raise EquipError("Этот предмет нельзя надеть в этот слот.")

        qty = int(inv.get("qty") or 0)
        if qty <= 0:
            raise EquipError("Этого предмета нет на складе.")

        col = SLOT_TO_COL[slot_key]
        eq = (
            await self._s.execute(
                text(
                    f"""
                    SELECT {col} AS cur_item_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).mappings().first()
        cur_item_id = int(eq.get("cur_item_id")) if eq and eq.get("cur_item_id") is not None else None

        if cur_item_id == int(item_id):
            return

        await self._s.execute(
            text(f"UPDATE equipment SET {col} = :iid WHERE character_id = :cid"),
            {"iid": int(item_id), "cid": int(character_id)},
        )

        if qty <= 1:
            await self._s.execute(
                text("DELETE FROM character_inventory WHERE character_id = :cid AND item_id = :iid"),
                {"cid": int(character_id), "iid": int(item_id)},
            )
        else:
            await self._s.execute(
                text(
                    """
                    UPDATE character_inventory
                    SET qty = qty - 1
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id)},
            )

        if cur_item_id is not None:
            await self._s.execute(
                text(
                    """
                    INSERT INTO character_inventory (character_id, item_id, qty)
                    VALUES (:cid, :iid, 1)
                    ON CONFLICT (character_id, item_id)
                    DO UPDATE SET qty = character_inventory.qty + 1
                    """
                ),
                {"cid": int(character_id), "iid": int(cur_item_id)},
            )

        await self._s.commit()

    async def unequip_armor(self, tg_id: int, character_id: int, slot_key: str) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        col = SLOT_TO_COL[slot_key]
        eq = (
            await self._s.execute(
                text(
                    f"""
                    SELECT {col} AS cur_item_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).mappings().first()

        cur_item_id = int(eq.get("cur_item_id")) if eq and eq.get("cur_item_id") is not None else None
        if cur_item_id is None:
            return

        await self._s.execute(
            text(f"UPDATE equipment SET {col} = NULL WHERE character_id = :cid"),
            {"cid": int(character_id)},
        )

        await self._s.execute(
            text(
                """
                INSERT INTO character_inventory (character_id, item_id, qty)
                VALUES (:cid, :iid, 1)
                ON CONFLICT (character_id, item_id)
                DO UPDATE SET qty = character_inventory.qty + 1
                """
            ),
            {"cid": int(character_id), "iid": int(cur_item_id)},
        )

        await self._s.commit()

    async def move_or_swap_weapon(self, tg_id: int, character_id: int, from_slot: int, to_slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        if from_slot not in (1, 2, 3) or to_slot not in (1, 2, 3) or from_slot == to_slot:
            raise EquipError("Некорректные слоты оружия.")

        eq = (
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
        if not eq:
            raise EquipError("Не удалось загрузить снаряжение.")

        w = {1: eq[0], 2: eq[1], 3: eq[2]}
        w_from = w.get(from_slot)
        w_to = w.get(to_slot)
        if w_from is None:
            raise EquipError("В этом слоте нет оружия.")

        col_from = f"weapon_{from_slot}_id"
        col_to = f"weapon_{to_slot}_id"

        if w_to is None:
            await self._s.execute(
                text(
                    f"""
                    UPDATE equipment
                    SET {col_to} = :w_from,
                        {col_from} = NULL
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id), "w_from": int(w_from)},
            )
        else:
            await self._s.execute(
                text(
                    f"""
                    UPDATE equipment
                    SET {col_to} = :w_from,
                        {col_from} = :w_to
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id), "w_from": int(w_from), "w_to": int(w_to)},
            )

        await self._s.commit()
