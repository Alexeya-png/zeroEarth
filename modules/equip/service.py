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
    MAX_AMMO_ON_CHARACTER = 9

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
                text(
                    """
                    SELECT 1
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
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

    async def _ensure_not_in_active_raid(self, character_id: int) -> None:
        row = (
            await self._s.execute(
                text("SELECT 1 FROM raids WHERE character_id = :cid AND status = 'active' LIMIT 1"),
                {"cid": int(character_id)},
            )
        ).first()
        if row:
            raise EquipError("Персонаж в рейде. Снаряжение недоступно.")

    async def _ensure_inventory_min_qty(self, character_id: int, item_id: int, min_qty: int = 1) -> None:
        if item_id is None:
            return
        await self._s.execute(
            text(
                """
                INSERT INTO character_inventory (character_id, item_id, qty)
                VALUES (:cid, :iid, :q)
                ON CONFLICT (character_id, item_id)
                DO UPDATE SET qty = GREATEST(character_inventory.qty, EXCLUDED.qty)
                """
            ),
            {"cid": int(character_id), "iid": int(item_id), "q": int(min_qty)},
        )

    async def _ensure_equipped_present_in_inventory(self, character_id: int, ids: list[int | None]) -> None:
        for iid in ids:
            if iid is None:
                continue
            await self._ensure_inventory_min_qty(int(character_id), int(iid), 1)

    def equip_text(self, view: EquipmentView) -> str:
        def row(k: str, v: str) -> str:
            return f"{k}: <b>{_esc(v) if v else '—'}</b>"

        return "\n".join(
            [
                f"<b>Снарядить бойца</b> – {_esc(view.character_name)}",
                "",
                row("Шлем", view.head),
                row("Торс", view.body),
                row("Перчатки", view.gloves),
                row("Ботинки", view.boots),
                "",
                row("Оружие 1", view.w1),
                row("Оружие 2", view.w2),
                row("Оружие 3", view.w3),
            ]
        )

    async def equipment_view(self, tg_id: int, character_id: int) -> EquipmentView:
        ch = await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        row = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      e.head_item_id, e.body_item_id, e.gloves_item_id, e.boots_item_id,
                      e.weapon_1_id, e.weapon_2_id, e.weapon_3_id
                    FROM equipment e
                    WHERE e.character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).first()
        if not row:
            raise EquipError("Не удалось загрузить экипировку.")

        head_id, body_id, gloves_id, boots_id, w1_id, w2_id, w3_id = row

        await self._ensure_equipped_present_in_inventory(
            int(character_id),
            [head_id, body_id, gloves_id, boots_id, w1_id, w2_id, w3_id],
        )
        await self._s.commit()

        async def item_name(item_id: int | None) -> str:
            if item_id is None:
                return ""
            r = (
                await self._s.execute(
                    text("SELECT name FROM items WHERE id = :id"),
                    {"id": int(item_id)},
                )
            ).first()
            return str(r[0]) if r else ""

        async def weapon_name(weapon_id: int | None) -> str:
            if weapon_id is None:
                return ""
            r = (
                await self._s.execute(
                    text("SELECT name FROM weapons WHERE id = :id"),
                    {"id": int(weapon_id)},
                )
            ).first()
            return str(r[0]) if r else ""

        head = await item_name(head_id)
        body = await item_name(body_id)
        gloves = await item_name(gloves_id)
        boots = await item_name(boots_id)

        w1 = await weapon_name(w1_id)
        w2 = await weapon_name(w2_id)
        w3 = await weapon_name(w3_id)

        weapons: dict[int, str] = {}
        if w1_id is not None:
            weapons[1] = w1
        if w2_id is not None:
            weapons[2] = w2
        if w3_id is not None:
            weapons[3] = w3

        return EquipmentView(
            character_id=int(ch["id"]),
            character_name=str(ch.get("name") or "Персонаж"),
            head=head,
            body=body,
            gloves=gloves,
            boots=boots,
            w1=w1,
            w2=w2,
            w3=w3,
            head_has_item=head_id is not None,
            body_has_item=body_id is not None,
            gloves_has_item=gloves_id is not None,
            boots_has_item=boots_id is not None,
            weapons=weapons,
        )

    async def list_armor_inventory(
        self, tg_id: int, character_id: int, slot_key: str, page: int, page_size: int
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        await self._get_character(tg_id, character_id)
        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        total = (
            await self._s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    WHERE ci.character_id = :cid AND i.item_type = :t AND ci.qty > 0
                    """
                ),
                {"cid": int(character_id), "t": slot_key},
            )
        ).scalar_one()

        if total <= 0:
            return ([], 0, 0)

        page = max(0, int(page))
        offset = page * page_size

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      i.id, i.name, ci.qty,
                      s.tier, s.armor, s.reliability
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    LEFT JOIN item_equipment_stats s ON s.item_id = i.id
                    WHERE ci.character_id = :cid AND i.item_type = :t AND ci.qty > 0
                    ORDER BY i.id
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"cid": int(character_id), "t": slot_key, "lim": int(page_size), "off": int(offset)},
            )
        ).mappings().all()

        return (rows, int(total), int(page))

    async def wear_armor(self, tg_id: int, character_id: int, slot_key: str, item_id: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        ok = (
            await self._s.execute(
                text(
                    """
                    SELECT 1
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    WHERE ci.character_id = :cid
                      AND ci.item_id = :iid
                      AND ci.qty > 0
                      AND i.item_type = :t
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id), "t": slot_key},
            )
        ).first()
        if not ok:
            raise EquipError("Предмет не найден на складе.")

        col = SLOT_TO_COL[slot_key]
        await self._s.execute(
            text(
                f"""
                UPDATE equipment
                SET {col} = :iid
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id), "iid": int(item_id)},
        )
        await self._s.commit()

    async def unequip_armor(self, tg_id: int, character_id: int, slot_key: str) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if slot_key not in SLOT_TO_COL:
            raise EquipError("Некорректный слот.")

        col = SLOT_TO_COL[slot_key]
        await self._s.execute(
            text(
                f"""
                UPDATE equipment
                SET {col} = NULL
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id)},
        )
        await self._s.commit()


    async def list_weapon_inventory(
        self, tg_id: int, character_id: int, page: int, page_size: int
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        total = (
            await self._s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    WHERE ci.character_id = :cid
                      AND ci.qty > 0
                    """
                ),
                {"cid": int(character_id)},
            )
        ).scalar_one()

        page = max(0, int(page))
        off = page * int(page_size)

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ci.item_id AS weapon_id,
                      ci.qty AS qty,
                      w.name AS name,
                      COALESCE(w.quality_tier, '') AS tier,
                      CASE
                        WHEN e.weapon_1_id = ci.item_id THEN 1
                        WHEN e.weapon_2_id = ci.item_id THEN 2
                        WHEN e.weapon_3_id = ci.item_id THEN 3
                        ELSE NULL
                      END AS equipped_slot
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    LEFT JOIN equipment e ON e.character_id = ci.character_id
                    WHERE ci.character_id = :cid
                      AND ci.qty > 0
                    ORDER BY
                      w.quality_tier DESC,
                      w.quality_score DESC,
                      w.id DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"cid": int(character_id), "lim": int(page_size), "off": int(off)},
            )
        ).mappings().all()

        return (list(rows), int(total or 0), int(page))

    async def weapon_pick_view(self, tg_id: int, character_id: int, to_slot: int) -> dict[int, str]:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if to_slot not in (1, 2, 3):
            raise EquipError("Некорректный слот.")

        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ci.item_id AS weapon_id,
                      w.name AS weapon_name
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    WHERE ci.character_id = :cid
                      AND ci.qty > 0
                    ORDER BY w.id
                    """
                ),
                {"cid": int(character_id)},
            )
        ).mappings().all()

        out: dict[int, str] = {}
        for r in rows:
            out[int(r["weapon_id"])] = str(r["weapon_name"])
        return out


    async def move_weapon_between_slots(self, tg_id: int, character_id: int, from_slot: int, to_slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if from_slot not in (1, 2, 3) or to_slot not in (1, 2, 3) or int(from_slot) == int(to_slot):
            raise EquipError("Некорректный слот.")

        row = (
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
        if not row:
            raise EquipError("Не удалось загрузить экипировку.")

        w1, w2, w3 = row
        slot_map = {1: w1, 2: w2, 3: w3}

        from_wid = slot_map.get(int(from_slot))
        to_wid = slot_map.get(int(to_slot))
        if from_wid is None:
            raise EquipError("В исходном слоте нет оружия.")

        if to_wid is not None:
            await self.ammo_clear(tg_id, character_id, int(to_slot))
        await self.ammo_clear(tg_id, character_id, int(from_slot))

        col_from = f"weapon_{int(from_slot)}_id"
        col_to = f"weapon_{int(to_slot)}_id"
        await self._s.execute(
            text(
                f"""
                UPDATE equipment
                SET {col_to} = :wid,
                    {col_from} = NULL
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id), "wid": int(from_wid)},
        )
        await self._s.commit()

    async def equip_weapon(self, tg_id: int, character_id: int, to_slot: int, weapon_id: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if to_slot not in (1, 2, 3):
            raise EquipError("Некорректный слот.")

        ok = (
            await self._s.execute(
                text(
                    """
                    SELECT 1
                    FROM character_inventory ci
                    JOIN weapons w ON w.id = ci.item_id
                    WHERE ci.character_id = :cid
                      AND ci.item_id = :wid
                      AND ci.qty > 0
                    """
                ),
                {"cid": int(character_id), "wid": int(weapon_id)},
            )
        ).first()
        if not ok:
            raise EquipError("Оружие не найдено на складе.")

        row = (
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
        if not row:
            raise EquipError("Не удалось загрузить экипировку.")

        w1, w2, w3 = row
        slot_map = {1: w1, 2: w2, 3: w3}

        current = slot_map.get(int(to_slot))
        if current is not None and int(current) == int(weapon_id):
            return

        if current is not None:
            await self.ammo_clear(tg_id, character_id, int(to_slot))

        for s, wid in slot_map.items():
            if int(s) != int(to_slot) and wid is not None and int(wid) == int(weapon_id):
                await self.ammo_clear(tg_id, character_id, int(s))

        # В Postgres нельзя присваивать одно и то же поле дважды в одном UPDATE.
        # Поэтому выставляем все 3 слота одним выражением CASE.
        await self._s.execute(
            text(
                """
                UPDATE equipment
                SET
                  weapon_1_id = CASE
                    WHEN :slot = 1 THEN :wid
                    WHEN weapon_1_id = :wid AND :slot <> 1 THEN NULL
                    ELSE weapon_1_id
                  END,
                  weapon_2_id = CASE
                    WHEN :slot = 2 THEN :wid
                    WHEN weapon_2_id = :wid AND :slot <> 2 THEN NULL
                    ELSE weapon_2_id
                  END,
                  weapon_3_id = CASE
                    WHEN :slot = 3 THEN :wid
                    WHEN weapon_3_id = :wid AND :slot <> 3 THEN NULL
                    ELSE weapon_3_id
                  END
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id), "wid": int(weapon_id), "slot": int(to_slot)},
        )
        await self._s.commit()

    async def unequip_weapon(self, tg_id: int, character_id: int, slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if slot not in (1, 2, 3):
            raise EquipError("Некорректный слот.")

        await self.ammo_clear(tg_id, character_id, int(slot))

        col = f"weapon_{int(slot)}_id"
        await self._s.execute(
            text(
                f"""
                UPDATE equipment
                SET {col} = NULL
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id)},
        )
        await self._s.commit()

    async def ammo_weapons(self, tg_id: int, character_id: int) -> dict[int, dict[str, Any]]:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        row = (
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
        if not row:
            return {}

        slots: dict[int, int] = {}
        for idx, wid in enumerate(row, start=1):
            if wid is not None:
                slots[idx] = int(wid)

        out: dict[int, dict[str, Any]] = {}
        for slot, wid in slots.items():
            w = (
                await self._s.execute(
                    text(
                        """
                        SELECT
                          w.id AS weapon_id,
                          w.name AS weapon_name,
                          w.caliber_id AS caliber_id,
                          c.code AS caliber_code,
                          COALESCE(c.name, '') AS caliber_name
                        FROM weapons w
                        JOIN calibers c ON c.id = w.caliber_id
                        WHERE w.id = :wid
                        """
                    ),
                    {"wid": int(wid)},
                )
            ).mappings().first()

            if not w:
                continue

            out[int(slot)] = {
                "weapon_id": int(w["weapon_id"]),
                "weapon_name": str(w["weapon_name"]),
                "caliber_id": int(w["caliber_id"]),
                "caliber_code": str(w["caliber_code"]),
                "caliber_name": str(w["caliber_name"] or ""),
            }

        return out

    async def ammo_total_loaded(self, character_id: int) -> int:
        total = (
            await self._s.execute(
                text(
                    """
                    SELECT COALESCE(SUM(qty), 0)
                    FROM character_ammo_loadout
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).scalar_one_or_none()
        return int(total or 0)

    async def ammo_slot_state(self, character_id: int, slot: int) -> tuple[int | None, int]:
        row = (
            await self._s.execute(
                text(
                    """
                    SELECT ammo_type_id, qty
                    FROM character_ammo_loadout
                    WHERE character_id = :cid AND weapon_slot = :slot
                    """
                ),
                {"cid": int(character_id), "slot": int(slot)},
            )
        ).first()
        if not row:
            return (None, 0)
        ammo_id, qty = row
        return (int(ammo_id) if ammo_id is not None else None, int(qty or 0))

    async def ammo_compatible_types(self, caliber_id: int) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT id, name, damage, armor_penetration
                    FROM ammo_types
                    WHERE caliber_id = :cid
                    ORDER BY id
                    """
                ),
                {"cid": int(caliber_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def ammo_inventory_qty(self, character_id: int, ammo_type_id: int) -> int:
        qty = (
            await self._s.execute(
                text(
                    """
                    SELECT COALESCE(SUM(ci.qty), 0)
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    JOIN ammo_types a ON a.id = :aid
                    JOIN calibers c ON c.id = a.caliber_id
                    WHERE ci.character_id = :cid
                      AND ci.qty > 0
                      AND i.item_type IN ('Ammo', 'misc')
                      AND (i.meta_json->>'kind') = 'ammo'
                      AND (i.meta_json->>'caliber_code') = c.code
                      AND (i.meta_json->>'bullet_type') = a.name
                    """
                ),
                {"cid": int(character_id), "aid": int(ammo_type_id)},
            )
        ).scalar_one_or_none()
        return int(qty or 0)

    async def _ammo_item_id_for_type(self, ammo_type_id: int) -> int:
        row = (
            await self._s.execute(
                text(
                    """
                    SELECT i.id
                    FROM items i
                    JOIN ammo_types a ON a.id = :aid
                    JOIN calibers c ON c.id = a.caliber_id
                    WHERE i.item_type IN ('Ammo', 'misc')
                      AND (i.meta_json->>'kind') = 'ammo'
                      AND (i.meta_json->>'caliber_code') = c.code
                      AND (i.meta_json->>'bullet_type') = a.name
                    ORDER BY i.id
                    LIMIT 1
                    """
                ),
                {"aid": int(ammo_type_id)},
            )
        ).first()
        if not row:
            raise EquipError("Предмет патронов не найден.")
        return int(row[0])

    async def _ammo_inventory_take(self, character_id: int, ammo_type_id: int, qty: int) -> None:
        if qty <= 0:
            return

        item_id = await self._ammo_item_id_for_type(int(ammo_type_id))

        row = (
            await self._s.execute(
                text(
                    """
                    SELECT qty
                    FROM character_inventory
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id)},
            )
        ).first()

        have = int(row[0]) if row and row[0] is not None else 0
        if have < int(qty):
            raise EquipError("Недостаточно патронов на складе.")

        new_qty = int(have) - int(qty)
        if new_qty > 0:
            await self._s.execute(
                text(
                    """
                    UPDATE character_inventory
                    SET qty = :q
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"cid": int(character_id), "iid": int(item_id), "q": int(new_qty)},
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

    async def _ammo_inventory_put(self, character_id: int, ammo_type_id: int, qty: int) -> None:
        if qty <= 0:
            return

        item_id = await self._ammo_item_id_for_type(int(ammo_type_id))

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

    async def ammo_set_type(self, tg_id: int, character_id: int, slot: int, ammo_type_id: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)

        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

        caliber_id = int(weapons[slot]["caliber_id"])
        ok = (
            await self._s.execute(
                text(
                    """
                    SELECT 1
                    FROM ammo_types
                    WHERE id = :aid AND caliber_id = :cid
                    """
                ),
                {"aid": int(ammo_type_id), "cid": int(caliber_id)},
            )
        ).first()
        if not ok:
            raise EquipError("Этот тип боеприпасов не подходит к оружию.")

        cur_ammo_id, cur_qty = await self.ammo_slot_state(character_id, slot)
        total = await self.ammo_total_loaded(character_id)

        new_qty = int(cur_qty)
        if new_qty <= 0:
            if total >= self.MAX_AMMO_ON_CHARACTER:
                raise EquipError("Максимум 9 боеприпасов на бойце.")
            new_qty = 1

        take_qty = 0
        put_old_qty = 0
        if cur_ammo_id is not None and int(cur_ammo_id) == int(ammo_type_id):
            take_qty = max(0, int(new_qty) - int(cur_qty))
        else:
            if cur_ammo_id is not None and int(cur_qty) > 0:
                put_old_qty = int(cur_qty)
            take_qty = int(new_qty)

        if int(take_qty) > 0:
            inv_qty = await self.ammo_inventory_qty(character_id, int(ammo_type_id))
            if int(inv_qty) < int(take_qty):
                raise EquipError("Недостаточно патронов на складе.")

        try:
            if put_old_qty > 0 and cur_ammo_id is not None:
                await self._ammo_inventory_put(character_id, int(cur_ammo_id), int(put_old_qty))

            if take_qty > 0:
                await self._ammo_inventory_take(character_id, int(ammo_type_id), int(take_qty))

            await self._s.execute(
                text(
                    """
                    INSERT INTO character_ammo_loadout (character_id, weapon_slot, ammo_type_id, qty)
                    VALUES (:cid, :slot, :aid, :qty)
                    ON CONFLICT (character_id, weapon_slot)
                    DO UPDATE SET ammo_type_id = EXCLUDED.ammo_type_id, qty = EXCLUDED.qty
                    """
                ),
                {"cid": int(character_id), "slot": int(slot), "aid": int(ammo_type_id), "qty": int(new_qty)},
            )
            await self._s.commit()
        except Exception:
            await self._s.rollback()
            raise

    async def ammo_add(self, tg_id: int, character_id: int, slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)

        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

        ammo_id, _ = await self.ammo_slot_state(character_id, slot)
        if ammo_id is None:
            raise EquipError("Сначала выбери тип боеприпаса.")

        total = await self.ammo_total_loaded(character_id)
        if total >= self.MAX_AMMO_ON_CHARACTER:
            raise EquipError("Максимум 9 боеприпасов на бойце.")

        inv_qty = await self.ammo_inventory_qty(character_id, int(ammo_id))
        if int(inv_qty) < 1:
            raise EquipError("На складе нет таких патронов.")

        try:
            await self._ammo_inventory_take(character_id, int(ammo_id), 1)

            await self._s.execute(
                text(
                    """
                    INSERT INTO character_ammo_loadout (character_id, weapon_slot, ammo_type_id, qty)
                    VALUES (:cid, :slot, :aid, 1)
                    ON CONFLICT (character_id, weapon_slot)
                    DO UPDATE SET qty = character_ammo_loadout.qty + 1
                    """
                ),
                {"cid": int(character_id), "slot": int(slot), "aid": int(ammo_id)},
            )
            await self._s.commit()
        except Exception:
            await self._s.rollback()
            raise

    async def ammo_sub(self, tg_id: int, character_id: int, slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)

        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

        ammo_id, qty = await self.ammo_slot_state(character_id, slot)
        if ammo_id is None or int(qty) <= 0:
            return

        new_qty = int(qty) - 1

        try:
            await self._ammo_inventory_put(character_id, int(ammo_id), 1)

            if new_qty <= 0:
                await self._s.execute(
                    text(
                        """
                        INSERT INTO character_ammo_loadout (character_id, weapon_slot, ammo_type_id, qty)
                        VALUES (:cid, :slot, NULL, 0)
                        ON CONFLICT (character_id, weapon_slot)
                        DO UPDATE SET ammo_type_id = NULL, qty = 0
                        """
                    ),
                    {"cid": int(character_id), "slot": int(slot)},
                )
            else:
                await self._s.execute(
                    text(
                        """
                        UPDATE character_ammo_loadout
                        SET qty = :q
                        WHERE character_id = :cid AND weapon_slot = :slot
                        """
                    ),
                    {"cid": int(character_id), "slot": int(slot), "q": int(new_qty)},
                )

            await self._s.commit()
        except Exception:
            await self._s.rollback()
            raise

    async def ammo_clear(self, tg_id: int, character_id: int, slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_not_in_active_raid(character_id)
        await self._ensure_equipment_row(character_id)

        if slot not in (1, 2, 3):
            raise EquipError("Некорректный слот.")

        ammo_id, qty = await self.ammo_slot_state(character_id, slot)

        try:
            if ammo_id is not None and int(qty) > 0:
                await self._ammo_inventory_put(character_id, int(ammo_id), int(qty))

            await self._s.execute(
                text(
                    """
                    INSERT INTO character_ammo_loadout (character_id, weapon_slot, ammo_type_id, qty)
                    VALUES (:cid, :slot, NULL, 0)
                    ON CONFLICT (character_id, weapon_slot)
                    DO UPDATE SET ammo_type_id = NULL, qty = 0
                    """
                ),
                {"cid": int(character_id), "slot": int(slot)},
            )
            await self._s.commit()
        except Exception:
            await self._s.rollback()
            raise
