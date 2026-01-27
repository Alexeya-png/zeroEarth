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
                    WHERE ci.character_id = :cid AND i.item_type = :t
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
                    WHERE ci.character_id = :cid AND i.item_type = :t
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

    async def weapon_pick_view(self, tg_id: int, character_id: int, to_slot: int) -> dict[int, str]:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        if to_slot not in (1, 2, 3):
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
            return {}

        out: dict[int, str] = {}
        for idx, wid in enumerate(row, start=1):
            if idx == to_slot:
                continue
            if wid is None:
                continue
            w = (
                await self._s.execute(
                    text("SELECT name FROM weapons WHERE id = :id"),
                    {"id": int(wid)},
                )
            ).first()
            out[idx] = str(w[0]) if w else "Оружие"
        return out

    async def move_weapon_between_slots(self, tg_id: int, character_id: int, from_slot: int, to_slot: int) -> None:
        await self._get_character(tg_id, character_id)
        await self._ensure_equipment_row(character_id)

        if from_slot not in (1, 2, 3) or to_slot not in (1, 2, 3) or from_slot == to_slot:
            raise EquipError("Некорректный слот.")

        col_from = f"weapon_{int(from_slot)}_id"
        col_to = f"weapon_{int(to_slot)}_id"

        row = (
            await self._s.execute(
                text(
                    f"""
                    SELECT {col_from}, {col_to}
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
        ).first()
        if not row:
            raise EquipError("Экипировка не найдена.")

        w_from = row[0]
        w_to = row[1]
        if w_from is None:
            raise EquipError("В выбранном слоте нет оружия.")

        await self._s.execute(
            text(
                f"""
                UPDATE equipment
                SET {col_to} = :w_from,
                    {col_from} = :w_to
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id), "w_from": int(w_from), "w_to": int(w_to) if w_to is not None else None},
        )

        await self._s.commit()

    async def ammo_weapons(self, tg_id: int, character_id: int) -> dict[int, dict[str, Any]]:
        await self._get_character(tg_id, character_id)

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
            return {}

        slots: dict[int, int] = {}
        for idx, wid in enumerate(eq, start=1):
            if wid is not None:
                slots[idx] = int(wid)

        out: dict[int, dict[str, Any]] = {}
        for slot, wid in slots.items():
            w = (
                await self._s.execute(
                    text(
                        """
                        SELECT
                          w.id, w.name,
                          w.caliber_id,
                          c.code,
                          COALESCE(c.name, '') AS caliber_name
                        FROM weapons w
                        JOIN calibers c ON c.id = w.caliber_id
                        WHERE w.id = :wid
                        """
                    ),
                    {"wid": int(wid)},
                )
            ).first()
            if not w:
                continue
            out[int(slot)] = {
                "weapon_id": int(w[0]),
                "weapon_name": str(w[1]),
                "caliber_id": int(w[2]),
                "caliber_code": str(w[3]),
                "caliber_name": str(w[4] or ""),
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
        return (row[0] if row[0] is not None else None, int(row[1] or 0))

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

    async def ammo_set_type(self, tg_id: int, character_id: int, slot: int, ammo_type_id: int) -> None:
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

        _, cur_qty = await self.ammo_slot_state(character_id, slot)
        total = await self.ammo_total_loaded(character_id)

        new_qty = cur_qty
        if new_qty <= 0:
            if total >= self.MAX_AMMO_ON_CHARACTER:
                raise EquipError("Максимум 9 боеприпасов на бойце.")
            new_qty = 1

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

    async def ammo_add(self, tg_id: int, character_id: int, slot: int) -> None:
        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

        ammo_id, _ = await self.ammo_slot_state(character_id, slot)
        if ammo_id is None:
            raise EquipError("Сначала выбери тип боеприпаса.")
        total = await self.ammo_total_loaded(character_id)
        if total >= self.MAX_AMMO_ON_CHARACTER:
            raise EquipError("Максимум 9 боеприпасов на бойце.")

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

    async def ammo_sub(self, tg_id: int, character_id: int, slot: int) -> None:
        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

        ammo_id, qty = await self.ammo_slot_state(character_id, slot)
        if ammo_id is None or qty <= 0:
            return

        new_qty = qty - 1
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

    async def ammo_clear(self, tg_id: int, character_id: int, slot: int) -> None:
        weapons = await self.ammo_weapons(tg_id, character_id)
        if slot not in weapons:
            raise EquipError("Оружие в этом слоте не найдено.")

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
