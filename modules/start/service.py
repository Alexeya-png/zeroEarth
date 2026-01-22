from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


FREE_START_COINS = 500
PREMIUM_START_COINS = 1500


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(x: Any) -> str:
    if x is None:
        return "–"
    if isinstance(x, Decimal):
        x = float(x)
    if isinstance(x, float):
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return f"{x:.1f}".rstrip("0").rstrip(".")
    return str(x)


@dataclass(frozen=True)
class UserInfo:
    id: int
    tg_id: int
    account_tier: str
    character_slots: int
    balance: int


class CreateCharacterError(Exception):
    pass


class StartService:
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

        return UserInfo(
            id=int(row["id"]),
            tg_id=int(row["tg_id"]),
            account_tier=str(row["account_tier"]),
            character_slots=int(row["character_slots"]),
            balance=int(row["balance"]),
        )

    async def list_characters(self, tg_id: int) -> List[Mapping[str, Any]]:
        user = await self.ensure_user(tg_id)
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      c.id, c.name, c.faction, c.creation_type, c.is_alive,
                      c.endurance, c.agility, c.intelligence,
                      c.hp AS max_hp,
                      COALESCE(h.current_hp, c.hp) AS current_hp,
                      c.accuracy, c.stealth
                    FROM characters c
                    LEFT JOIN character_health h ON h.character_id = c.id
                    WHERE c.user_id = :uid
                    ORDER BY c.id DESC
                    """
                ),
                {"uid": user.id},
            )
        ).mappings().all()
        return list(rows)

    async def characters_summary_text(self, tg_id: int) -> str:
        rows = await self.list_characters(tg_id)
        if not rows:
            return "<b>Мои персонажи</b>\nПерсонажей нет."

        lines: list[str] = ["<b>Мои персонажи</b>"]
        for r in rows:
            name = _esc(str(r["name"] or "Без имени"))
            status = "жив" if r["is_alive"] else "мертв"
            lines.append(
                f"#{r['id']} – {name} – {r['creation_type']} – {status}\n"
                f"Здоровье – HP: {r['current_hp']}/{r['max_hp']}  Точность: {r['accuracy']}  Скрытность: {_num(r['stealth'])}\n"
                f"Выносливость: {r['endurance']}  Ловкость: {r['agility']}  Интеллект: {r['intelligence']}"
            )
            lines.append("")
        return "\n".join(lines).rstrip()

    async def create_character(self, tg_id: int, creation_type: str, name: str) -> str:
        user = await self.ensure_user(tg_id)

        if creation_type not in ("free", "premium"):
            raise CreateCharacterError("Некорректный тип создания.")

        if creation_type == "premium" and user.account_tier != "premium":
            raise CreateCharacterError("Premium создание доступно только для premium аккаунта.")

        alive_cnt = (
            await self._s.execute(
                text("SELECT COUNT(*) FROM characters WHERE user_id = :uid AND is_alive = TRUE"),
                {"uid": user.id},
            )
        ).scalar_one()

        if int(alive_cnt) >= int(user.character_slots):
            raise CreateCharacterError(f"Лимит персонажей – {alive_cnt}/{user.character_slots}")

        points = 3 if creation_type == "free" else 4
        stats = self._roll_base_stats(points)
        derived = self._calc_derived(stats["endurance"], stats["agility"], stats["intelligence"])

        faction = "civilians"

        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO characters (
                      user_id, name, faction, is_alive, creation_type,
                      endurance, agility, intelligence,
                      hp, carry_capacity, load,
                      reaction, accuracy, initiative, stealth,
                      tech_training, hacking, loot_analysis, loot_modding, repair, chem_modding
                    )
                    VALUES (
                      :user_id, :name, :faction, TRUE, :creation_type,
                      :endurance, :agility, :intelligence,
                      :hp, :carry_capacity, :load,
                      :reaction, :accuracy, :initiative, :stealth,
                      :tech_training, :hacking, :loot_analysis, :loot_modding, :repair, :chem_modding
                    )
                    RETURNING id, hp
                    """
                ),
                {
                    "user_id": user.id,
                    "name": name,
                    "faction": faction,
                    "creation_type": creation_type,
                    **stats,
                    **derived,
                },
            )
        ).mappings().first()

        char_id = int(row["id"])
        max_hp = int(row["hp"])

        await self._s.execute(
            text("INSERT INTO equipment (character_id) VALUES (:cid)"),
            {"cid": char_id},
        )

        await self._s.execute(
            text(
                """
                INSERT INTO character_health (character_id, current_hp)
                VALUES (:cid, :hp)
                ON CONFLICT (character_id) DO NOTHING
                """
            ),
            {"cid": char_id, "hp": max_hp},
        )

        if creation_type == "free":
            loadout = await self._pick_free_loadout()
            coins = FREE_START_COINS
        else:
            loadout = await self._pick_premium_loadout()
            coins = PREMIUM_START_COINS

        await self._apply_loadout(char_id, loadout)

        await self._s.execute(
            text("UPDATE users SET balance = balance + :coins WHERE id = :uid"),
            {"coins": coins, "uid": user.id},
        )

        await self._s.commit()
        return await self.character_details_text(tg_id, char_id, coins_added=coins)

    async def character_details_text(self, tg_id: int, character_id: int, coins_added: int = 0) -> str:
        user = await self.ensure_user(tg_id)

        ch = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      c.id, c.name, c.faction, c.creation_type, c.is_alive,
                      c.endurance, c.agility, c.intelligence,
                      c.hp AS max_hp,
                      COALESCE(h.current_hp, c.hp) AS current_hp,
                      h.recovery_until,
                      h.head_injury, h.torso_injury, h.arm_injury, h.leg_injury,
                      c.carry_capacity, c.load,
                      c.reaction, c.accuracy, c.initiative, c.stealth,
                      c.tech_training, c.hacking, c.loot_analysis, c.repair
                    FROM characters c
                    LEFT JOIN character_health h ON h.character_id = c.id
                    WHERE c.id = :cid AND c.user_id = :uid
                    """
                ),
                {"cid": character_id, "uid": user.id},
            )
        ).mappings().first()

        if not ch:
            raise CreateCharacterError("Персонаж не найден.")

        eq = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ih.name AS head_name, sh.tier AS head_tier, sh.armor AS head_armor, sh.reliability AS head_rel,
                      ib.name AS body_name, sb.tier AS body_tier, sb.armor AS body_armor, sb.reliability AS body_rel,
                      ig.name AS gloves_name, sg.tier AS gloves_tier, sg.reliability AS gloves_rel,
                      it.name AS boots_name, st.tier AS boots_tier, st.reliability AS boots_rel,
                      w1.name AS w1_name, w1.quality_tier AS w1_tier, w1.accuracy AS w1_acc, w1.reliability AS w1_rel,
                      c1.name AS w1_caliber
                    FROM equipment e
                    LEFT JOIN items ih ON ih.id = e.head_item_id
                    LEFT JOIN item_equipment_stats sh ON sh.item_id = ih.id
                    LEFT JOIN items ib ON ib.id = e.body_item_id
                    LEFT JOIN item_equipment_stats sb ON sb.item_id = ib.id
                    LEFT JOIN items ig ON ig.id = e.gloves_item_id
                    LEFT JOIN item_equipment_stats sg ON sg.item_id = ig.id
                    LEFT JOIN items it ON it.id = e.boots_item_id
                    LEFT JOIN item_equipment_stats st ON st.item_id = it.id
                    LEFT JOIN weapons w1 ON w1.id = e.weapon_1_id
                    LEFT JOIN calibers c1 ON c1.id = w1.caliber_id
                    WHERE e.character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().first()

        def fmt_item(n: Optional[str], t: Optional[str], armor: Any = None, rel: Any = None) -> str:
            if not n:
                return "–"
            name = _esc(str(n))
            tier = t or "–"
            if armor is None and rel is None:
                return f"{name} – {tier}"
            a = _num(armor) if armor is not None else "–"
            r = _num(rel) if rel is not None else "–"
            return f"{name} – {tier} – Броня {a} – Надёжность {r}"

        def fmt_weapon() -> str:
            if not eq or not eq["w1_name"]:
                return "–"
            name = _esc(str(eq["w1_name"]))
            tier = eq["w1_tier"] or "–"
            cal = _esc(str(eq["w1_caliber"] or "–"))
            return f"{name} – {tier} – {cal} – Точность {eq['w1_acc']} – Надёжность {eq['w1_rel']}"

        name = _esc(str(ch["name"] or "Без имени"))
        status = "жив" if ch["is_alive"] else "мертв"

        text_out = "\n".join(
            [
                f"<b>Персонаж</b> #{ch['id']} – {name}",
                f"<b>Фракция</b> – {ch['faction']}",
                f"<b>Создание</b> – {ch['creation_type']}",
                f"<b>Статус</b> – {status}",
                "",
                "<b>Базовые параметры</b>",
                "<pre>"
                f"Выносливость: {ch['endurance']}\n"
                f"Ловкость:      {ch['agility']}\n"
                f"Интеллект:     {ch['intelligence']}"
                "</pre>",
                "<b>Характеристики</b>",
                "<pre>"
                f"Здоровье – HP:               {ch['current_hp']}/{ch['max_hp']}\n"
                f"Грузоподъёмность:            {_num(ch['carry_capacity'])}\n"
                f"Нагрузка:                    {_num(ch['load'])}\n"
                f"Реакция:                     {_num(ch['reaction'])}\n"
                f"Точность:                    {ch['accuracy']}\n"
                f"Инициатива:                  {_num(ch['initiative'])}\n"
                f"Скрытность:                  {_num(ch['stealth'])}\n"
                f"Техподготовка:               {ch['tech_training']}\n"
                f"Взлом:                       {ch['hacking']}\n"
                f"Анализ и идентификация лута: {ch['loot_analysis']}\n"
                f"Ремонт и модификации:        {ch['repair']}"
                "</pre>",
                "<b>Травмы</b>",
                "<pre>"
                f"Травма головы:     {ch['head_injury']}\n"
                f"Травма туловища:   {ch['torso_injury']}\n"
                f"Травма руки:       {ch['arm_injury']}\n"
                f"Травма ноги:       {ch['leg_injury']}"
                "</pre>",
                "<b>Снаряжение</b>",
                f"Голова – {fmt_item(eq['head_name'] if eq else None, eq['head_tier'] if eq else None, eq['head_armor'] if eq else None, eq['head_rel'] if eq else None)}",
                f"Тело – {fmt_item(eq['body_name'] if eq else None, eq['body_tier'] if eq else None, eq['body_armor'] if eq else None, eq['body_rel'] if eq else None)}",
                f"Перчатки – {fmt_item(eq['gloves_name'] if eq else None, eq['gloves_tier'] if eq else None, None, eq['gloves_rel'] if eq else None)}",
                f"Ботинки – {fmt_item(eq['boots_name'] if eq else None, eq['boots_tier'] if eq else None, None, eq['boots_rel'] if eq else None)}",
                "",
                "<b>Оружие</b>",
                fmt_weapon(),
            ]
        )

        if coins_added:
            text_out += f"\n\n<b>Начислено монет</b> – {coins_added}"

        return text_out

    def _roll_base_stats(self, points: int) -> Dict[str, int]:
        stats = {"endurance": 2, "agility": 2, "intelligence": 2}
        keys = list(stats.keys())
        while points > 0:
            candidates = [k for k in keys if stats[k] < 4]
            stats[random.choice(candidates)] += 1
            points -= 1
        return stats

    def _calc_derived(self, endurance: int, agility: int, intelligence: int) -> Dict[str, Any]:
        return {
            "hp": 100 + (endurance * 2),
            "carry_capacity": float(70 + (endurance * 2.5)),
            "load": 0.0,
            "reaction": float(2 + (agility * 0.5)),
            "accuracy": 15 + agility,
            "initiative": float(agility * 0.5),
            "stealth": float(1 + (agility * 0.5)),
            "tech_training": intelligence,
            "hacking": intelligence,
            "loot_analysis": 1 + intelligence,
            "loot_modding": intelligence,
            "repair": intelligence,
            "chem_modding": intelligence,
        }

    async def _pick_weapon(self, tier: str, category: Optional[str] = None) -> int:
        sql = "SELECT id FROM weapons WHERE quality_tier = :tier"
        params: Dict[str, Any] = {"tier": tier}
        if category:
            sql += " AND category = :cat"
            params["cat"] = category
        sql += " ORDER BY random() LIMIT 1"

        row = (await self._s.execute(text(sql), params)).first()
        if not row:
            raise CreateCharacterError(f"Нет оружия тира {tier}.")
        return int(row[0])

    async def _pick_item(self, item_type: str, tier: str) -> int:
        row = (
            await self._s.execute(
                text(
                    """
                    SELECT i.id
                    FROM items i
                    JOIN item_equipment_stats s ON s.item_id = i.id
                    WHERE i.item_type = :t AND s.tier = :tier
                    ORDER BY random()
                    LIMIT 1
                    """
                ),
                {"t": item_type, "tier": tier},
            )
        ).first()
        if not row:
            raise CreateCharacterError(f"Нет предметов {item_type} тира {tier}.")
        return int(row[0])

    async def _pick_free_loadout(self) -> Dict[str, Optional[int]]:
        weapon_id = await self._pick_weapon("D", category="pistol")
        body_id = await self._pick_item("body", "D")

        extra_slot = random.choice(["head", "gloves", "boots"])
        extra_item_id = await self._pick_item(extra_slot, "D")

        loadout: Dict[str, Optional[int]] = {
            "head_item_id": None,
            "body_item_id": body_id,
            "gloves_item_id": None,
            "boots_item_id": None,
            "weapon_1_id": weapon_id,
            "weapon_2_id": None,
            "weapon_3_id": None,
        }

        if extra_slot == "head":
            loadout["head_item_id"] = extra_item_id
        elif extra_slot == "gloves":
            loadout["gloves_item_id"] = extra_item_id
        else:
            loadout["boots_item_id"] = extra_item_id

        return loadout

    async def _pick_premium_loadout(self) -> Dict[str, Optional[int]]:
        weapon_id = await self._pick_weapon("C")
        upgraded_slot = random.choice(["head", "body", "gloves", "boots"])

        head_tier = "C" if upgraded_slot == "head" else "D"
        body_tier = "C" if upgraded_slot == "body" else "D"
        gloves_tier = "C" if upgraded_slot == "gloves" else "D"
        boots_tier = "C" if upgraded_slot == "boots" else "D"

        head_id = await self._pick_item("head", head_tier)
        body_id = await self._pick_item("body", body_tier)
        gloves_id = await self._pick_item("gloves", gloves_tier)
        boots_id = await self._pick_item("boots", boots_tier)

        return {
            "head_item_id": head_id,
            "body_item_id": body_id,
            "gloves_item_id": gloves_id,
            "boots_item_id": boots_id,
            "weapon_1_id": weapon_id,
            "weapon_2_id": None,
            "weapon_3_id": None,
        }

    async def _apply_loadout(self, character_id: int, loadout: Dict[str, Optional[int]]) -> None:
        await self._s.execute(
            text(
                """
                UPDATE equipment
                SET head_item_id = :head_item_id,
                    body_item_id = :body_item_id,
                    gloves_item_id = :gloves_item_id,
                    boots_item_id = :boots_item_id,
                    weapon_1_id = :weapon_1_id,
                    weapon_2_id = :weapon_2_id,
                    weapon_3_id = :weapon_3_id
                WHERE character_id = :cid
                """
            ),
            {"cid": character_id, **loadout},
        )
