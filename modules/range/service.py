from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.mechanics.shooting import CAT_RU, calc_shooting_model, simulate_series


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class AmmoInfo:
    id: int
    name: str
    damage: int
    armor_penetration: int


@dataclass(frozen=True)
class WeaponInfo:
    slot: int
    id: int
    name: str
    category: str
    accuracy: int
    reliability: int
    caliber_id: int
    caliber_code: str
    caliber_name: str
    weight_kg: float
    ammo: List[AmmoInfo]


@dataclass
class SessionWeapon:
    attacks: int = 0
    shots: int = 0
    hits: int = 0
    jams: int = 0
    total_damage: int = 0
    max_damage: int = 0

    last_attempts: int = 0
    last_hp_end: int = 100
    last_total_damage: int = 0
    last_hits_by_zone: Dict[str, int] = field(default_factory=lambda: {"head": 0, "torso": 0, "arm": 0, "leg": 0})
    last_inj: Dict[str, int] = field(default_factory=lambda: {"head": 0, "torso": 0, "arm": 0, "leg": 0})
    last_max_recovery_hours: float = 0.0


# (tg_id, character_id) -> weapon_id -> session data
_SESSIONS: Dict[Tuple[int, int], Dict[int, SessionWeapon]] = {}


MANNEQUIN_HP = 100
MANNEQUIN_REA = 0
MANNEQUIN_DEF_BASE = 20.0
MANNEQUIN_REL_ARMOR = 100.0


class RangeService:
    def __init__(self, session: AsyncSession):
        self._s = session

    def _session_key(self, tg_id: int, character_id: int) -> Tuple[int, int]:
        return (int(tg_id), int(character_id))

    def _clear_session(self, tg_id: int, character_id: int) -> None:
        _SESSIONS.pop(self._session_key(tg_id, character_id), None)

    def _get_session_weapon(self, tg_id: int, character_id: int, weapon_id: int) -> SessionWeapon:
        key = self._session_key(tg_id, character_id)
        wmap = _SESSIONS.setdefault(key, {})
        return wmap.setdefault(int(weapon_id), SessionWeapon())

    async def _ensure_user(self, tg_id: int) -> int:
        row = (
            await self._s.execute(
                text("SELECT id FROM users WHERE tg_id = :tg_id"),
                {"tg_id": tg_id},
            )
        ).first()
        if row:
            return int(row[0])

        row = (
            await self._s.execute(
                text("INSERT INTO users (tg_id) VALUES (:tg_id) RETURNING id"),
                {"tg_id": tg_id},
            )
        ).first()
        return int(row[0])

    async def _get_character(self, tg_id: int, character_id: int) -> Mapping[str, Any]:
        uid = await self._ensure_user(tg_id)
        row = (
            await self._s.execute(
                text(
                    """
                    SELECT id, name, accuracy
                    FROM characters
                    WHERE id = :cid AND user_id = :uid
                    """
                ),
                {"cid": character_id, "uid": uid},
            )
        ).mappings().first()
        if not row:
            raise ValueError("character_not_found")
        return row

    async def _get_weapons(self, character_id: int) -> Dict[int, WeaponInfo]:
        eq = (
            await self._s.execute(
                text(
                    """
                    SELECT weapon_1_id, weapon_2_id, weapon_3_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).first()
        if not eq:
            return {}

        slots: Dict[int, int] = {}
        for idx, wid in enumerate(eq, start=1):
            if wid is not None:
                slots[idx] = int(wid)

        out: Dict[int, WeaponInfo] = {}
        for slot, wid in slots.items():
            w = (
                await self._s.execute(
                    text(
                        """
                        SELECT
                          w.id, w.name, w.category, w.accuracy, w.reliability,
                          w.caliber_id, w.weight_kg,
                          c.code, COALESCE(c.name, c.code)
                        FROM weapons w
                        JOIN calibers c ON c.id = w.caliber_id
                        WHERE w.id = :wid
                        """
                    ),
                    {"wid": wid},
                )
            ).first()
            if not w:
                continue

            ammo_rows = (
                await self._s.execute(
                    text(
                        """
                        SELECT id, name, damage, armor_penetration
                        FROM ammo_types
                        WHERE caliber_id = :cid
                        ORDER BY id
                        """
                    ),
                    {"cid": int(w[5])},
                )
            ).all()
            ammo = [AmmoInfo(int(a[0]), str(a[1]), int(a[2]), int(a[3])) for a in ammo_rows]

            out[slot] = WeaponInfo(
                slot=slot,
                id=int(w[0]),
                name=str(w[1]),
                category=str(w[2]),
                accuracy=int(w[3]),
                reliability=int(w[4]),
                caliber_id=int(w[5]),
                caliber_code=str(w[7]),
                caliber_name=str(w[8]),
                weight_kg=float(w[6] or 0),
                ammo=ammo,
            )
        return out

    def _fmt_zones(self, d: Dict[str, int]) -> str:
        return f"{d['head']} {d['torso']} {d['arm']} {d['leg']}"

    def _range_text(
        self,
        tg_id: int,
        character_id: int,
        ch: Mapping[str, Any],
        weapon: Optional[WeaponInfo],
        session_w: Optional[SessionWeapon],
    ) -> str:
        name = esc(str(ch.get("name") or "Без имени"))
        lines: List[str] = [f"<b>Тир</b> – Персонаж #{int(ch['id'])} – {name}", ""]

        lines.append("<b>Манекен</b>")
        lines.append(
            "<pre>"
            f"HP: {MANNEQUIN_HP}/{MANNEQUIN_HP}\n"
            f"Броня: {MANNEQUIN_DEF_BASE:.0f}%\n"
            f"Реакция: {MANNEQUIN_REA}\n"
            f"Состояние брони: {MANNEQUIN_REL_ARMOR:.0f}%"
            "</pre>"
        )

        if not weapon:
            lines += ["<b>Оружие</b>", "На персонаже нет оружия."]
            return "\n".join(lines).rstrip()

        ammo = weapon.ammo[0] if weapon.ammo else AmmoInfo(0, "–", 0, 0)

        ACCc = float(ch.get("accuracy") or 0)
        ACCw = float(weapon.accuracy)
        RELw = float(weapon.reliability)
        DMG = float(ammo.damage)
        AP = float(ammo.armor_penetration)

        c = calc_shooting_model(
            ACCc=ACCc,
            ACCw=ACCw,
            RELw=RELw,
            CAT=weapon.category,
            DMG=DMG,
            AP=AP,
            REAd=float(MANNEQUIN_REA),
            DEFbase=float(MANNEQUIN_DEF_BASE),
            RELarmor=float(MANNEQUIN_REL_ARMOR),
        )

        cat_ru = CAT_RU.get(weapon.category, weapon.category)

        lines.append("<b>Оружие</b>")
        lines.append(
            "<pre>"
            f"Слот: {weapon.slot}\n"
            f"Название: {esc(weapon.name)}\n"
            f"Тип: {esc(cat_ru)}\n"
            f"Калибр: {esc(weapon.caliber_code)} – {esc(weapon.caliber_name)}\n"
            f"Точность оружия: {weapon.accuracy}\n"
            f"Надёжность оружия: {weapon.reliability}%\n"
            f"Вес: {weapon.weight_kg:.2f} кг"
            "</pre>"
        )

        lines.append("<b>Патрон</b>")
        lines.append(f"<pre>{esc(ammo.name)}\nУрон: {ammo.damage}\nПробитие: {ammo.armor_penetration}</pre>")

        if weapon.ammo and len(weapon.ammo) > 1:
            lines.append("<b>Доступные патроны</b>")
            lines.append(
                "<pre>"
                + "\n".join(
                    [
                        f"{a.id} – {esc(a.name)} – урон {a.damage} – пробитие {a.armor_penetration}"
                        for a in weapon.ammo
                    ]
                )
                + "</pre>"
            )

        lines.append("<b>Шансы по манекену</b>")
        lines.append(
            "<pre>"
            f"Попадание: {c['p_hit']*100:.1f}%\n"
            f"Осечка: {c['p_jam']*100:.1f}%\n"
            f"Выстрелов за атаку: {int(c['shots'])}\n"
            f"Урон при попадании: {c['d_hit']:.1f}\n"
            f"Ожидаемый урон за атаку: {c['exp_damage']:.1f}"
            "</pre>"
        )

        sw = session_w or SessionWeapon()
        hit_rate = (sw.hits / sw.shots) if sw.shots else 0.0
        jam_rate = (sw.jams / sw.shots) if sw.shots else 0.0
        avg_attack = (sw.total_damage / sw.attacks) if sw.attacks else 0.0

        lines.append("<b>Статистика – текущий заход в тир</b>")
        lines.append(
            "<pre>"
            f"Атак: {sw.attacks}\n"
            f"Выстрелов: {sw.shots}\n"
            f"Попаданий: {sw.hits}\n"
            f"Осечек: {sw.jams}\n"
            f"Точность: {hit_rate*100:.1f}%\n"
            f"Осечки: {jam_rate*100:.1f}%\n"
            f"Урон всего: {sw.total_damage}\n"
            f"Урон средний за атаку: {avg_attack:.2f}\n"
            f"Макс урон за серию: {sw.max_damage}"
            "</pre>"
        )

        if sw.last_attempts > 0:
            lines.append("<b>Последняя серия</b>")
            lines.append(
                "<pre>"
                f"Атак: {sw.last_attempts}\n"
                f"Урон: {sw.last_total_damage}\n"
                f"HP манекена: {sw.last_hp_end}/{MANNEQUIN_HP}\n"
                f"Попадания по зонам – голова корпус рука нога: {self._fmt_zones(sw.last_hits_by_zone)}\n"
                f"Травмы – голова корпус рука нога: {self._fmt_zones(sw.last_inj)}\n"
                f"Макс добавка восстановления: {sw.last_max_recovery_hours:.1f}ч"
                "</pre>"
            )
        else:
            lines.append("<b>Последняя серия</b>")
            lines.append("<pre>Пока нет выстрелов</pre>")

        return "\n".join(lines).rstrip()

    async def range_view(self, tg_id: int, character_id: int, selected_slot: Optional[int]) -> Dict[str, Any]:
        ch = await self._get_character(tg_id, character_id)

        weapons = await self._get_weapons(int(ch["id"]))
        slots = {1: 1 in weapons, 2: 2 in weapons, 3: 3 in weapons}

        if selected_slot is None:
            self._clear_session(tg_id, int(ch["id"]))

        if selected_slot is None or selected_slot not in weapons:
            selected_slot = 1 if 1 in weapons else 2 if 2 in weapons else 3 if 3 in weapons else 1

        weapon = weapons.get(selected_slot)
        session_w = self._get_session_weapon(tg_id, int(ch["id"]), weapon.id) if weapon else None

        return {
            "text": self._range_text(tg_id, int(ch["id"]), ch, weapon, session_w),
            "selected_slot": selected_slot,
            "slots": slots,
        }

    async def range_shoot(self, tg_id: int, character_id: int, slot: int, attempts: int = 5) -> Dict[str, Any]:
        ch = await self._get_character(tg_id, character_id)

        weapons = await self._get_weapons(int(ch["id"]))
        slots = {1: 1 in weapons, 2: 2 in weapons, 3: 3 in weapons}

        weapon = weapons.get(slot)
        if not weapon:
            return {
                "text": self._range_text(tg_id, int(ch["id"]), ch, None, None),
                "selected_slot": slot,
                "slots": slots,
            }

        ammo = weapon.ammo[0] if weapon.ammo else AmmoInfo(0, "–", 0, 0)

        ACCc = float(ch.get("accuracy") or 0)
        ACCw = float(weapon.accuracy)
        RELw = float(weapon.reliability)
        DMG = float(ammo.damage)
        AP = float(ammo.armor_penetration)

        c = calc_shooting_model(
            ACCc=ACCc,
            ACCw=ACCw,
            RELw=RELw,
            CAT=weapon.category,
            DMG=DMG,
            AP=AP,
            REAd=float(MANNEQUIN_REA),
            DEFbase=float(MANNEQUIN_DEF_BASE),
            RELarmor=float(MANNEQUIN_REL_ARMOR),
        )

        shots = int(c["shots"])
        p_hit = float(c["p_hit"])
        p_jam = float(c["p_jam"])
        d_hit = float(c["d_hit"])

        sim = simulate_series(
            attempts=int(attempts),
            shots=int(shots),
            p_hit=float(p_hit),
            p_jam=float(p_jam),
            d_hit=float(d_hit),
            category=str(weapon.category),
            mannequin_hp=int(MANNEQUIN_HP),
            mannequin_hp_max=int(MANNEQUIN_HP),
        )

        mannequin_hp_end = int(sim["mannequin_hp_end"])
        hits_by_zone = dict(sim["hits_by_zone"])  # type: ignore[arg-type]
        inj = dict(sim["inj"])  # type: ignore[arg-type]
        max_recovery_hours = float(sim["max_recovery_hours"])
        total_damage = int(sim["total_damage"])
        total_hits = int(sim["total_hits"])
        total_jams = int(sim["total_jams"])
        per_attack_damage = list(sim["per_attack_damage"])  # type: ignore[arg-type]
        attempt_rows = list(sim["attempt_rows"])  # type: ignore[arg-type]

        max_series = max(per_attack_damage) if per_attack_damage else 0

        sw = self._get_session_weapon(tg_id, int(ch["id"]), weapon.id)
        sw.attacks += attempts
        sw.shots += attempts * shots
        sw.hits += total_hits
        sw.jams += total_jams
        sw.total_damage += total_damage
        sw.max_damage = max(sw.max_damage, max_series)

        sw.last_attempts = attempts
        sw.last_hp_end = mannequin_hp_end
        sw.last_total_damage = total_damage
        sw.last_hits_by_zone = dict(hits_by_zone)
        sw.last_inj = dict(inj)
        sw.last_max_recovery_hours = float(max_recovery_hours)

        name = esc(str(ch.get("name") or "Без имени"))
        cat_ru = CAT_RU.get(weapon.category, weapon.category)

        hit_rate = (total_hits / (attempts * shots)) if (attempts * shots) else 0.0
        jam_rate = (total_jams / (attempts * shots)) if (attempts * shots) else 0.0
        avg_attack = (total_damage / attempts) if attempts else 0.0

        text_out = "\n".join(
            [
                f"<b>Тир</b> – Персонаж #{int(ch['id'])} – {name}",
                "",
                "<b>Оружие</b>",
                "<pre>"
                f"{esc(weapon.name)} – {esc(cat_ru)}\n"
                f"Калибр: {esc(weapon.caliber_code)}\n"
                f"Патрон: {esc(ammo.name)} – урон {ammo.damage} – пробитие {ammo.armor_penetration}"
                "</pre>",
                "<b>Шансы по манекену</b>",
                "<pre>"
                f"Попадание: {c['p_hit']*100:.1f}%\n"
                f"Осечка: {c['p_jam']*100:.1f}%\n"
                f"Выстрелов за атаку: {shots}\n"
                f"Урон при попадании: {c['d_hit']:.1f}\n"
                f"Ожидаемый урон за атаку: {c['exp_damage']:.1f}"
                "</pre>",
                "<b>Серия</b>",
                "<pre>" + "\n".join(esc(x) for x in attempt_rows) + "</pre>",
                "<b>Итоги серии</b>",
                "<pre>"
                f"Атак: {attempts}\n"
                f"Выстрелов: {attempts*shots}\n"
                f"Попаданий: {total_hits}\n"
                f"Осечек: {total_jams}\n"
                f"Точность: {hit_rate*100:.1f}%\n"
                f"Осечки: {jam_rate*100:.1f}%\n"
                f"Урон всего: {total_damage}\n"
                f"Урон средний за атаку: {avg_attack:.2f}\n"
                f"HP манекена: {mannequin_hp_end}/{MANNEQUIN_HP}\n"
                f"Попадания по зонам – голова корпус рука нога: {hits_by_zone['head']} {hits_by_zone['torso']} {hits_by_zone['arm']} {hits_by_zone['leg']}\n"
                f"Травмы – голова корпус рука нога: {inj['head']} {inj['torso']} {inj['arm']} {inj['leg']}\n"
                f"Макс добавка восстановления: {max_recovery_hours:.1f}ч"
                "</pre>",
            ]
        ).rstrip()

        return {"text": text_out, "selected_slot": slot, "slots": slots}
