from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo


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


def _to_float(x: Any) -> float:
    if x is None:
        return 0.0
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except Exception:
        return 0.0


def _fmt_kg(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))} кг"
    return f"{x:.1f} кг".replace(".0", " кг")


def _fmt_pct(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))}%"
    return f"{x:.1f}%".replace(".0%", "%")


def _fmt_timedelta_ru(seconds: int) -> str:
    seconds = max(0, int(seconds))
    mins = seconds // 60
    hrs = mins // 60
    days = hrs // 24
    mins = mins % 60
    hrs = hrs % 24

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hrs:
        parts.append(f"{hrs}ч")
    if mins and not days:
        parts.append(f"{mins}м")
    if not parts:
        parts.append("0м")
    return " ".join(parts)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class UserInfo:
    id: int
    tg_id: int
    account_tier: str
    character_slots: int
    balance: int


@dataclass(frozen=True)
class PhysicalStateView:
    text: str
    image_path: Optional[str] = None


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

        await self._s.commit()

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
                    SELECT id, name, faction, is_alive, created_at
                    FROM characters
                    WHERE user_id = :uid
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"uid": user.id},
            )
        ).mappings().all()
        return list(rows)

    async def characters_summary_text(self, tg_id: int) -> str:
        chars = await self.list_characters(tg_id)
        if not chars:
            return "У тебя нет персонажей."

        lines = ["<b>Твои персонажи</b>"]
        for ch in chars:
            cid = int(ch["id"])
            name = _esc(str(ch["name"] or "Без имени"))
            faction = _esc(str(ch["faction"]))
            status = "жив" if ch["is_alive"] else "мертв"
            lines.append(f"#{cid} – {name} – {faction} – {status}")
        return "\n".join(lines)

    async def create_character(self, tg_id: int, creation_type: str, name: str) -> str:
        user = await self.ensure_user(tg_id)

        row = (
            await self._s.execute(
                text("SELECT COUNT(*) AS cnt FROM characters WHERE user_id = :uid"),
                {"uid": user.id},
            )
        ).mappings().first()
        used = int(row["cnt"] or 0)
        if used >= int(user.character_slots):
            raise CreateCharacterError("Лимит слотов персонажей исчерпан.")

        base = self._roll_base_stats(points=3)
        derived = self._calc_derived(base["endurance"], base["agility"], base["intelligence"])

        coins = FREE_START_COINS if creation_type == "free" else PREMIUM_START_COINS

        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO characters (
                      user_id, name, faction, creation_type, is_alive,
                      endurance, agility, intelligence,
                      hp, carry_capacity, load,
                      reaction, accuracy, initiative, stealth,
                      tech_training, hacking, loot_analysis, loot_modding, repair, chem_modding
                    )
                    VALUES (
                      :user_id, :name, :faction, :creation_type, TRUE,
                      :endurance, :agility, :intelligence,
                      :hp, :carry_capacity, :load,
                      :reaction, :accuracy, :initiative, :stealth,
                      :tech_training, :hacking, :loot_analysis, :loot_modding, :repair, :chem_modding
                    )
                    RETURNING id
                    """
                ),
                {
                    "user_id": user.id,
                    "name": name,
                    "faction": "civilians",
                    "creation_type": creation_type,
                    **base,
                    **derived,
                },
            )
        ).first()
        if not row:
            raise CreateCharacterError("Не удалось создать персонажа.")
        cid = int(row[0])

        await self._s.execute(text("INSERT INTO equipment (character_id) VALUES (:cid)"), {"cid": cid})
        await self._s.execute(text("INSERT INTO character_faction_profile (character_id) VALUES (:cid)"), {"cid": cid})

        loadout = await (self._pick_free_loadout() if creation_type == "free" else self._pick_premium_loadout())
        await self._apply_loadout(cid, loadout)

        # стартовые патроны как обычный предмет на складе
        try:
            await self._give_start_ammo(
                character_id=cid,
                weapon_id=loadout.get("weapon_1_id"),
                creation_type=creation_type,
            )
        except Exception:
            # патроны не должны ломать создание персонажа
            pass

        await self._s.execute(
            text("UPDATE users SET balance = balance + :coins WHERE id = :uid"),
            {"coins": coins, "uid": user.id},
        )

        await self._s.commit()

        summary = "\n".join(
            [
                "<b>Персонаж создан</b>",
                f"#{cid} – {_esc(name)}",
                "",
                "<b>Базовые параметры</b>",
                "<pre>"
                f"Выносливость: {base['endurance']}\n"
                f"Ловкость:      {base['agility']}\n"
                f"Интеллект:     {base['intelligence']}"
                "</pre>",
                "<b>Характеристики</b>",
                "<pre>"
                f"HP: {int(derived['hp'])}\n"
                f"Грузоподъёмность: {_num(derived['carry_capacity'])}\n"
                f"Реакция: {_num(derived['reaction'])}\n"
                f"Точность: {derived['accuracy']}"
                "</pre>",
                f"<b>Начислено монет</b> – {coins}",
            ]
        )
        return summary

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
                      c.carry_capacity, c.load,
                      c.reaction, c.accuracy, c.initiative, c.stealth,
                      c.tech_training, c.hacking, c.loot_analysis, c.repair,

                      COALESCE(sh.accuracy_bonus, 0) + COALESCE(sb.accuracy_bonus, 0) + COALESCE(sg.accuracy_bonus, 0) + COALESCE(st.accuracy_bonus, 0) AS acc_bonus,
                      COALESCE(sh.reaction_bonus, 0) + COALESCE(sb.reaction_bonus, 0) + COALESCE(sg.reaction_bonus, 0) + COALESCE(st.reaction_bonus, 0) AS reaction_bonus,
                      COALESCE(sh.initiative_bonus, 0) + COALESCE(sb.initiative_bonus, 0) + COALESCE(sg.initiative_bonus, 0) + COALESCE(st.initiative_bonus, 0) AS initiative_bonus,
                      COALESCE(sh.stealth_bonus, 0) + COALESCE(sb.stealth_bonus, 0) + COALESCE(sg.stealth_bonus, 0) + COALESCE(st.stealth_bonus, 0) AS stealth_bonus,
                      COALESCE(sh.carry_capacity_bonus, 0) + COALESCE(sb.carry_capacity_bonus, 0) + COALESCE(sg.carry_capacity_bonus, 0) + COALESCE(st.carry_capacity_bonus, 0) AS carry_bonus,
                      COALESCE(sh.loot_analysis_bonus, 0) + COALESCE(sb.loot_analysis_bonus, 0) + COALESCE(sg.loot_analysis_bonus, 0) + COALESCE(st.loot_analysis_bonus, 0) AS loot_bonus
                    FROM characters c
                    LEFT JOIN character_health h ON h.character_id = c.id
                    LEFT JOIN equipment e ON e.character_id = c.id

                    LEFT JOIN item_equipment_stats sh ON sh.item_id = e.head_item_id
                    LEFT JOIN item_equipment_stats sb ON sb.item_id = e.body_item_id
                    LEFT JOIN item_equipment_stats sg ON sg.item_id = e.gloves_item_id
                    LEFT JOIN item_equipment_stats st ON st.item_id = e.boots_item_id

                    WHERE c.id = :cid AND c.user_id = :uid
                    """
                ),
                {"cid": character_id, "uid": user.id},
            )
        ).mappings().first()

        if not ch:
            raise CreateCharacterError("Персонаж не найден.")

        name = _esc(str(ch["name"] or "Без имени"))
        status = "жив" if ch["is_alive"] else "мертв"

        base_carry = _to_float(ch["carry_capacity"])
        base_reaction = _to_float(ch["reaction"])
        base_initiative = _to_float(ch["initiative"])
        base_stealth = _to_float(ch["stealth"])
        base_accuracy = int(ch["accuracy"])
        base_loot = int(ch["loot_analysis"])

        b_carry = _to_float(ch["carry_bonus"])
        b_reaction = _to_float(ch["reaction_bonus"])
        b_initiative = _to_float(ch["initiative_bonus"])
        b_stealth = _to_float(ch["stealth_bonus"])
        b_acc = int(ch["acc_bonus"])
        b_loot = int(ch["loot_bonus"])

        carry_eff = base_carry + b_carry
        reaction_eff = base_reaction + b_reaction
        initiative_eff = base_initiative + b_initiative
        stealth_eff = base_stealth + b_stealth
        accuracy_eff = base_accuracy + b_acc
        loot_eff = base_loot + b_loot

        LABEL_W = 18  # ширина колонки названий, чтобы всё ровно стояло

        def row(label: str, value: str) -> str:
            return f"{label:<{LABEL_W}}: {value}"

        def val_num(base_v: float, eff_v: float) -> str:
            b = _num(base_v)
            e = _num(eff_v)
            return b if b == e else f"{b} → {e}"

        def val_int(base_v: int, eff_v: int) -> str:
            return str(base_v) if base_v == eff_v else f"{base_v} → {eff_v}"

        pre_lines = [
            row("Здоровье – HP", f"{int(ch['current_hp'])}/{int(ch['max_hp'])}"),
            row("Грузоподъёмность", val_num(base_carry, carry_eff)),
            row("Нагрузка", _num(ch["load"])),
            row("Реакция", val_num(base_reaction, reaction_eff)),
            row("Точность", val_int(base_accuracy, accuracy_eff)),
            row("Инициатива", val_num(base_initiative, initiative_eff)),
            row("Скрытность", val_num(base_stealth, stealth_eff)),
            row("Техподготовка", str(ch["tech_training"])),
            row("Взлом", str(ch["hacking"])),
            row("Анализ лута", val_int(base_loot, loot_eff)),
            row("Ремонт/модиф.", str(ch["repair"])),
        ]

        text_out = "\n".join(
            [
                f"<b>Персонаж</b> #{ch['id']} – {name}",
                f"Фракция – {ch['faction']}",
                f"Создание – {ch['creation_type']}",
                f"Статус – {status}",
                "",
                "<b>Базовые параметры</b>",
                "<pre>"
                f"Выносливость: {ch['endurance']}\n"
                f"Ловкость:      {ch['agility']}\n"
                f"Интеллект:     {ch['intelligence']}"
                "</pre>",
                "<b>Характеристики</b>",
                "<pre>" + "\n".join(pre_lines) + "</pre>",
            ]
        )

        if coins_added:
            text_out += f"\n\n<b>Начислено монет</b> – {coins_added}"

        return text_out

    async def character_physical_state(self, tg_id: int, character_id: int) -> PhysicalStateView:
        user = await self.ensure_user(tg_id)

        ch = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      c.id, c.name,
                      c.hp AS max_hp,
                      COALESCE(h.current_hp, c.hp) AS current_hp,
                      h.recovery_until,
                      COALESCE(h.head_injury, 0) AS head_injury,
                      COALESCE(h.torso_injury, 0) AS torso_injury,
                      COALESCE(h.arm_injury, 0) AS arm_injury,
                      COALESCE(h.leg_injury, 0) AS leg_injury
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

        name = _esc(str(ch["name"] or "Без имени"))
        max_hp = int(ch["max_hp"])
        current_hp = int(ch["current_hp"])
        current_hp = max(0, min(max_hp, current_hp))
        pct = int(round((current_hp / max_hp) * 100)) if max_hp > 0 else 0

        recovery_until = ch.get("recovery_until")
        recovery_line = "Восстановление – –"

        if recovery_until is not None:
            dt: Any = recovery_until
            if isinstance(dt, str):
                s = dt.strip().replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(s)
                except Exception:
                    dt = None

            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if dt <= now:
                    recovery_line = "Восстановление – завершено"
                else:
                    left = int((dt - now).total_seconds())
                    try:
                        dt_loc = dt.astimezone(ZoneInfo("Europe/Stockholm"))
                        when = dt_loc.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        when = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
                    recovery_line = f"Восстановление – осталось {_fmt_timedelta_ru(left)} – до {when}"
            else:
                recovery_line = f"Восстановление – {str(recovery_until)}"

        injuries = {
            "Голова": int(ch["head_injury"]),
            "Туловище": int(ch["torso_injury"]),
            "Рука": int(ch["arm_injury"]),
            "Нога": int(ch["leg_injury"]),
        }
        has_inj = any(v > 0 for v in injuries.values())

        lines: list[str] = [
            f"<b>Физическое состояние</b> #{ch['id']} – {name}",
            f"HP: {current_hp}/{max_hp} – {pct}%",
            recovery_line,
            "",
            "<b>Травмы</b>",
        ]

        if not has_inj:
            lines.append("Травм нет")
        else:
            for k, v in injuries.items():
                if v > 0:
                    lines.append(f"{k} – {v}")

        text_out = "\n".join(lines).rstrip()

        image_path: Optional[str] = None
        try:
            root = _project_root()
            out_dir = root / "data" / "health"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"health_{user.tg_id}_{int(ch['id'])}.png"

            from scripts.render_health_overlay import Injuries as Inj, render_health_overlay

            render_health_overlay(
                inp=str(root / "assets" / "body.png"),
                bg=str(root / "assets" / "art.png"),
                out=str(out_path),
                max_hp=max_hp,
                current_hp=current_hp,
                injuries=Inj(
                    head=int(ch["head_injury"]),
                    torso=int(ch["torso_injury"]),
                    arm=int(ch["arm_injury"]),
                    leg=int(ch["leg_injury"]),
                ),
            )
            image_path = str(out_path)
        except Exception:
            image_path = None

        return PhysicalStateView(text=text_out, image_path=image_path)

    async def character_equipment_text(self, tg_id: int, character_id: int) -> str:
        user = await self.ensure_user(tg_id)

        base = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      c.id, c.name,
                      c.carry_capacity,
                      c.reaction, c.initiative, c.stealth
                    FROM characters c
                    WHERE c.id = :cid AND c.user_id = :uid
                    """
                ),
                {"cid": character_id, "uid": user.id},
            )
        ).mappings().first()

        if not base:
            raise CreateCharacterError("Персонаж не найден.")

        eq = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ih.name AS head_name, sh.tier AS head_tier, sh.armor AS head_armor, sh.reliability AS head_rel, ih.weight AS head_weight,
                      sh.reaction_bonus AS head_reaction_bonus, sh.initiative_bonus AS head_initiative_bonus, sh.stealth_bonus AS head_stealth_bonus, sh.carry_capacity_bonus AS head_carry_bonus,

                      ib.name AS body_name, sb.tier AS body_tier, sb.armor AS body_armor, sb.reliability AS body_rel, ib.weight AS body_weight,
                      sb.reaction_bonus AS body_reaction_bonus, sb.initiative_bonus AS body_initiative_bonus, sb.stealth_bonus AS body_stealth_bonus, sb.carry_capacity_bonus AS body_carry_bonus,

                      ig.name AS gloves_name, sg.tier AS gloves_tier, sg.armor AS gloves_armor, sg.reliability AS gloves_rel, ig.weight AS gloves_weight,
                      sg.reaction_bonus AS gloves_reaction_bonus, sg.initiative_bonus AS gloves_initiative_bonus, sg.stealth_bonus AS gloves_stealth_bonus, sg.carry_capacity_bonus AS gloves_carry_bonus,

                      it.name AS boots_name, st.tier AS boots_tier, st.armor AS boots_armor, st.reliability AS boots_rel, it.weight AS boots_weight,
                      st.reaction_bonus AS boots_reaction_bonus, st.initiative_bonus AS boots_initiative_bonus, st.stealth_bonus AS boots_stealth_bonus, st.carry_capacity_bonus AS boots_carry_bonus,

                      w1.name AS w1_name, w1.category AS w1_cat, w1.weight_kg AS w1_weight, w1.caliber_id AS w1_cal_id, w1.reliability AS w1_rel,
                      c1.code AS w1_cal_code, c1.name AS w1_cal_name,

                      w2.name AS w2_name, w2.category AS w2_cat, w2.weight_kg AS w2_weight, w2.caliber_id AS w2_cal_id, w2.reliability AS w2_rel,
                      c2.code AS w2_cal_code, c2.name AS w2_cal_name,

                      w3.name AS w3_name, w3.category AS w3_cat, w3.weight_kg AS w3_weight, w3.caliber_id AS w3_cal_id, w3.reliability AS w3_rel,
                      c3.code AS w3_cal_code, c3.name AS w3_cal_name
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
                    LEFT JOIN weapons w2 ON w2.id = e.weapon_2_id
                    LEFT JOIN calibers c2 ON c2.id = w2.caliber_id
                    LEFT JOIN weapons w3 ON w3.id = e.weapon_3_id
                    LEFT JOIN calibers c3 ON c3.id = w3.caliber_id

                    WHERE e.character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().first()

        def _slot_item(name: Any, tier: Any) -> str:
            if not name:
                return "–"
            t = tier if tier else "–"
            return f"{_esc(str(name))}, {t}"

        def _clamp_0_100(v: Any) -> float:
            try:
                x = float(v)
            except Exception:
                x = 0.0
            return max(0.0, min(100.0, x))

        head_armor_pct = _clamp_0_100(eq["head_armor"] if eq else 0)
        body_armor_pct = _clamp_0_100(eq["body_armor"] if eq else 0)
        defense_pct = 100.0 - ((100.0 - head_armor_pct) * (100.0 - body_armor_pct) / 100.0)
        defense_pct = max(1.0, defense_pct)

        rel_vals: list[float] = []
        if eq:
            for k in ("head_rel", "body_rel", "gloves_rel", "boots_rel", "w1_rel", "w2_rel", "w3_rel"):
                v = eq.get(k)
                if v is None:
                    continue
                vv = _clamp_0_100(v)
                if vv <= 0:
                    continue
                rel_vals.append(max(1.0, vv))

        if rel_vals:
            logs = [math.log(v / 100.0) for v in rel_vals]
            overall_rel = math.exp(sum(logs) / len(logs)) * 100.0
        else:
            overall_rel = 100.0

        total_weight = 0.0
        if eq:
            total_weight += _to_float(eq.get("head_weight"))
            total_weight += _to_float(eq.get("body_weight"))
            total_weight += _to_float(eq.get("gloves_weight"))
            total_weight += _to_float(eq.get("boots_weight"))
            total_weight += _to_float(eq.get("w1_weight"))
            total_weight += _to_float(eq.get("w2_weight"))
            total_weight += _to_float(eq.get("w3_weight"))

        base_carry = _to_float(base["carry_capacity"])
        base_reaction = _to_float(base["reaction"])
        base_initiative = _to_float(base["initiative"])
        base_stealth = _to_float(base["stealth"])

        bonus_reaction = 0.0
        bonus_initiative = 0.0
        bonus_stealth = 0.0
        bonus_carry = 0.0

        if eq:
            bonus_reaction = (
                _to_float(eq.get("head_reaction_bonus"))
                + _to_float(eq.get("body_reaction_bonus"))
                + _to_float(eq.get("gloves_reaction_bonus"))
                + _to_float(eq.get("boots_reaction_bonus"))
            )
            bonus_initiative = (
                _to_float(eq.get("head_initiative_bonus"))
                + _to_float(eq.get("body_initiative_bonus"))
                + _to_float(eq.get("gloves_initiative_bonus"))
                + _to_float(eq.get("boots_initiative_bonus"))
            )
            bonus_stealth = (
                _to_float(eq.get("head_stealth_bonus"))
                + _to_float(eq.get("body_stealth_bonus"))
                + _to_float(eq.get("gloves_stealth_bonus"))
                + _to_float(eq.get("boots_stealth_bonus"))
            )
            bonus_carry = (
                _to_float(eq.get("head_carry_bonus"))
                + _to_float(eq.get("body_carry_bonus"))
                + _to_float(eq.get("gloves_carry_bonus"))
                + _to_float(eq.get("boots_carry_bonus"))
            )

        carry_capacity = base_carry + bonus_carry
        if carry_capacity <= 0:
            carry_capacity = 0.0

        reaction_with_gear = base_reaction + bonus_reaction
        initiative_with_gear = base_initiative + bonus_initiative
        stealth_with_gear = base_stealth + bonus_stealth

        penalty = 0.0
        if carry_capacity > 0 and total_weight > carry_capacity:
            excess_pct = (total_weight / carry_capacity - 1.0) * 100.0
            steps = int(excess_pct // 10.0)
            penalty = 10.0 + max(0, steps - 1) * 5.0

        def _apply_pen(x: float) -> float:
            if penalty <= 0:
                return x
            return x * (1.0 - penalty / 100.0)

        reaction_final = _apply_pen(reaction_with_gear)
        initiative_final = _apply_pen(initiative_with_gear)
        stealth_final = _apply_pen(stealth_with_gear)

        def _line(label: str, base_val: float, final_val: float) -> str:
            if abs(final_val - base_val) < 1e-9:
                return f"{label}: {_num(base_val)} → {_num(base_val)}"
            return f"{label}: {_num(base_val)} → {_num(final_val)}"

        def _cal_text(code: Any, name: Any) -> str:
            if code:
                return str(code)
            if name:
                return str(name)
            return "–"

        async def _ammo_stats(caliber_id: Any) -> tuple[str, str]:
            if not caliber_id:
                return "–", "–"
            row = (
                await self._s.execute(
                    text(
                        """
                        SELECT damage, armor_penetration
                        FROM ammo_types
                        WHERE caliber_id = :cid
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {"cid": caliber_id},
                )
            ).mappings().first()
            if not row:
                return "–", "–"
            return str(row.get("damage") if row.get("damage") is not None else "–"), str(
                row.get("armor_penetration") if row.get("armor_penetration") is not None else "–"
            )

        w1_dmg, w1_ap = await _ammo_stats(eq.get("w1_cal_id") if eq else None)
        w2_dmg, w2_ap = await _ammo_stats(eq.get("w2_cal_id") if eq else None)
        w3_dmg, w3_ap = await _ammo_stats(eq.get("w3_cal_id") if eq else None)

        def _weapon_line(slot: int, name: Any, cat: Any, cal_code: Any, cal_name: Any, dmg: str, ap: str) -> str:
            if not name:
                return f"{slot} Слот – –"
            nm = _esc(str(name))
            tp = str(cat) if cat else "–"
            cal = _esc(_cal_text(cal_code, cal_name))
            return f"{slot} Слот – {nm}, {tp}, {cal}, урон {dmg}, пробитие {ap}"

        title = f"<b>Снаряжение</b> #{base['id']} – {_esc(str(base['name'] or 'Без имени'))}"

        combat = "\n".join(
            [
                "<b>Боевые характеристики</b>",
                "<pre>"
                f"Защита: {_fmt_pct(defense_pct)}\n"
                f"Надёжность: {_fmt_pct(overall_rel)}\n"
                f"Общий вес: {_fmt_kg(total_weight)} / {_fmt_kg(carry_capacity)}\n"
                f"Штраф: {_fmt_pct(penalty)}\n"
                f"{_line('Реакция', base_reaction, reaction_final)}\n"
                f"{_line('Инициатива', base_initiative, initiative_final)}\n"
                f"{_line('Скрытность', base_stealth, stealth_final)}"
                "</pre>",
            ]
        )

        equip_block = "\n".join(
            [
                "<b>Снаряжение</b>",
                "<pre>"
                f"Голова:   {_slot_item(eq['head_name'], eq['head_tier']) if eq else '–'}\n"
                f"Тело:     {_slot_item(eq['body_name'], eq['body_tier']) if eq else '–'}\n"
                f"Перчатки: {_slot_item(eq['gloves_name'], eq['gloves_tier']) if eq else '–'}\n"
                f"Ботинки:  {_slot_item(eq['boots_name'], eq['boots_tier']) if eq else '–'}"
                "</pre>",
            ]
        )

        weapons_block = "\n".join(
            [
                "<b>Оружие</b>",
                "<pre>"
                f"{_weapon_line(1, eq.get('w1_name') if eq else None, eq.get('w1_cat') if eq else None, eq.get('w1_cal_code') if eq else None, eq.get('w1_cal_name') if eq else None, w1_dmg, w1_ap)}\n"
                f"{_weapon_line(2, eq.get('w2_name') if eq else None, eq.get('w2_cat') if eq else None, eq.get('w2_cal_code') if eq else None, eq.get('w2_cal_name') if eq else None, w2_dmg, w2_ap)}\n"
                f"{_weapon_line(3, eq.get('w3_name') if eq else None, eq.get('w3_cat') if eq else None, eq.get('w3_cal_code') if eq else None, eq.get('w3_cal_name') if eq else None, w3_dmg, w3_ap)}"
                "</pre>",
            ]
        )

        return "\n\n".join([title, combat, equip_block, weapons_block])

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

            "reaction": float(10 + (0.5 * agility)),
            "accuracy": 15 + agility,
            "initiative": float(10 + (0.5 * agility)),
            "stealth": float(10 + (0.5 * agility)),

            "tech_training": int(10 * intelligence),
            "hacking": int(10 * intelligence),
            "loot_analysis": int(10 * intelligence),
            "loot_modding": int(10 * intelligence),
            "repair": int(10 * intelligence),
            "chem_modding": int(10 * intelligence),
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

    async def _give_start_ammo(self, character_id: int, weapon_id: Optional[int], creation_type: str) -> None:
        if not weapon_id:
            return

        w = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      w.category,
                      c.code AS caliber_code,
                      COALESCE(c.name, c.code) AS caliber_name
                    FROM weapons w
                    JOIN calibers c ON c.id = w.caliber_id
                    WHERE w.id = :wid
                    """
                ),
                {"wid": int(weapon_id)},
            )
        ).mappings().first()

        if not w:
            return

        caliber_code = str(w.get("caliber_code") or "")
        caliber_name = str(w.get("caliber_name") or caliber_code)
        category = str(w.get("category") or "")

        # тип патронов по умолчанию
        bullet_type = "Buckshot" if caliber_code == "12ga" else "FMJ"

        # количество патронов на старте
        if creation_type == "premium":
            qty = 80
        else:
            qty = 40

        # небольшая корректировка под категории
        if category in ("lmg",):
            qty = max(qty, 120)
        elif category in ("smg",):
            qty = max(qty, 90)
        elif category in ("shotgun",):
            qty = max(24, min(qty, 60))

        item_name = f"Патроны {caliber_name} {bullet_type}"

        item = (
            await self._s.execute(
                text("SELECT id FROM items WHERE name = :name"),
                {"name": item_name},
            )
        ).first()

        if not item:
            return

        item_id = int(item[0])

        await self._s.execute(
            text(
                """
                INSERT INTO character_inventory (character_id, item_id, qty)
                VALUES (:cid, :iid, :qty)
                ON CONFLICT (character_id, item_id)
                DO UPDATE SET qty = character_inventory.qty + EXCLUDED.qty
                """
            ),
            {"cid": int(character_id), "iid": item_id, "qty": int(qty)},
        )
