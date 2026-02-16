from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.mechanics.clash import InjuryState, simulate_clash_round
from core.mechanics.shooting import calc_shooting_model, simulate_series

_RNG = random.Random()
LOG = logging.getLogger(__name__)

_NOTIFY_DEFAULT_KINDS: set[str] = {
    "fight_started",
    "ammo_empty",
    "corpse_search",
    "item_found",
    "exit_started",
    "raid_finished",
    "raid_dead",
}


def _event_text(kind: str, payload: dict) -> str:
    try:
        if kind == "travel_started":
            pt = str(payload.get("point_name") or "")
            if not pt:
                pid = payload.get("point_id")
                if pid is not None:
                    pt = f"Точка {int(pid)}"
            return f"Переход к {pt}".strip()
        if kind == "search_started":
            pt = str(payload.get("point_name") or "")
            if not pt:
                pid = payload.get("point_id")
                if pid is not None:
                    pt = f"Точка {int(pid)}"
            return f"Начинает поиск на {pt}".strip()
        if kind == "heard_shots":
            return "Слышит стрельбу неподалёку."
        if kind == "fight_started":
            opp = str(payload.get("opponent_name") or "противником")
            return f"Вступает в бой с {opp}."
        if kind == "ammo_empty":
            wn = payload.get("weapon_name")
            if wn:
                return f"Закончились патроны – {wn}."
            return "Закончились патроны."
        if kind == "item_found":
            nm = str(payload.get("item_name") or "предмет")
            qty = int(payload.get("qty") or 1)
            return f"Находит: {nm} x{qty}."
        if kind == "corpse_search":
            victim = str(payload.get("victim_name") or "цель")
            found = str(payload.get("found_line") or "")
            if found:
                return f"Обыскивает {victim}. Находит: {found}."
            return f"Обыскивает {victim}."
        if kind == "fight_finished":
            outcome = str(payload.get("outcome") or "")
            if outcome == "kill":
                target = payload.get("loser_name") or "противника"
                return f"Бой окончен. Убивает {target}."
            if outcome == "mutual_kill":
                return "Бой окончен. Оба бойца погибли."
            return "Бой окончен."
        if kind == "exit_started":
            return "Начинает выход из рейда."
        if kind == "raid_finished":
            return "Рейд завершён."
        if kind == "raid_dead":
            return "Рейд провален – персонаж погиб."
    except Exception:
        LOG.debug("format_event_text failed", exc_info=True)
    return ""


async def _delete_message_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 60) -> None:
    try:
        await asyncio.sleep(max(1, int(delay_seconds)))
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        return


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        if v is None or v == "":
            return default
        return int(v)
    except Exception:
        return default


def _rand_minutes(a: int, b: int) -> int:
    return _RNG.randint(a, b)


def _travel_eta(now: datetime) -> datetime:
    seconds = _env_int("RAID_TRAVEL_SECONDS", 20)
    if seconds > 0:
        return now + timedelta(seconds=seconds)
    return now + timedelta(minutes=_rand_minutes(15, 30))


def _search_eta(now: datetime) -> datetime:
    seconds = _env_int("RAID_SEARCH_SECONDS", 0)
    if seconds > 0:
        return now + timedelta(seconds=seconds)
    return now + timedelta(minutes=_rand_minutes(10, 20))


def _minutes_between(a: datetime | None, b: datetime | None) -> int:
    if not a or not b:
        return 1
    secs = (b - a).total_seconds()
    if secs <= 0:
        return 1
    return max(1, int(secs // 60))


@dataclass
class WeaponSnapshot:
    slot: int
    name: str
    category: str
    caliber: str
    accuracy: int
    reliability: int
    dmg: int
    ap: int


@dataclass
class CombatantState:
    character_id: int
    name: str
    hp_max: int
    hp_current: int
    accuracy: int
    reaction: float
    initiative: float
    stealth: float
    defense_base_pct: float
    rel_armor_pct: float
    weapons: list[WeaponSnapshot]
    behavior: str
    injuries: InjuryState


class RaidsEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def tick(self) -> None:
        now = _utcnow()
        await self._apply_natural_recovery(now)
        await self._resolve_finished_fights(now)
        await self._advance_travel(now)
        await self._maybe_start_fights(now)
        await self._finish_searches(now)
        await self._finish_exits(now)

    async def _apply_natural_recovery(self, now: datetime) -> None:
        await self.db.execute(
            text(
                """
                WITH candidates AS (
                    SELECT
                        ch.character_id,
                        ch.current_hp,
                        c.hp AS max_hp,
                        ch.updated_at,
                        floor(extract(epoch from (:now - ch.updated_at)) / 600)::int AS ticks
                    FROM character_health ch
                    JOIN characters c ON c.id = ch.character_id
                    WHERE ch.current_hp < c.hp
                      AND ch.updated_at IS NOT NULL
                      AND floor(extract(epoch from (:now - ch.updated_at)) / 600)::int >= 1
                      AND NOT EXISTS (
                          SELECT 1
                          FROM raids r
                          WHERE r.character_id = ch.character_id
                            AND r.status = 'active'
                      )
                ),
                calc AS (
                    SELECT
                        character_id,
                        max_hp,
                        LEAST(max_hp, current_hp + ticks) AS new_hp,
                        (updated_at + (ticks * interval '600 seconds')) AS new_updated_at
                    FROM candidates
                )
                UPDATE character_health ch
                SET
                    current_hp = calc.new_hp,
                    updated_at = calc.new_updated_at,
                    recovery_until = CASE
                        WHEN calc.new_hp < calc.max_hp
                            THEN calc.new_updated_at + ((calc.max_hp - calc.new_hp) * interval '600 seconds')
                        ELSE NULL
                    END
                FROM calc
                WHERE ch.character_id = calc.character_id;
                """
            ),
            {"now": now},
        )

    async def _advance_travel(self, now: datetime) -> None:
        rows = await self.db.execute(
            text(
                """
                SELECT rp.id AS presence_id, rp.raid_id, rp.character_id, rp.point_id
                FROM raid_point_presence rp
                JOIN raids r ON r.id = rp.raid_id
                WHERE r.status = 'active'
                  AND rp.state = 'traveling'
                  AND rp.travel_eta_at IS NOT NULL
                  AND rp.travel_eta_at <= :now
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now},
        )
        presences = rows.mappings().all()
        for p in presences:
            presence_id = int(p["presence_id"])
            raid_id = int(p["raid_id"])
            character_id = int(p["character_id"])
            point_id = int(p["point_id"])

            eta = _search_eta(now)

            await self.db.execute(
                text(
                    """
                    UPDATE raid_point_presence
                    SET state = 'searching',
                        arrived_at = COALESCE(arrived_at, :now),
                        search_started_at = :now,
                        search_eta_at = :eta
                    WHERE id = :id
                    """
                ),
                {"id": presence_id, "now": now, "eta": eta},
            )
            await self.db.execute(
                text("UPDATE raids SET phase = 'searching', current_point_id = :p WHERE id = :r"),
                {"r": raid_id, "p": point_id},
            )
            await self._log(
                raid_id,
                character_id,
                point_id,
                presence_id,
                None,
                "search_started",
                {"search_eta_at": eta.isoformat(), "point_id": int(point_id)},
            )

    async def _maybe_start_fights(self, now: datetime) -> None:
        groups = await self.db.execute(
            text(
                """
                SELECT rp.point_id, COUNT(*) AS n
                FROM raid_point_presence rp
                JOIN raids r ON r.id = rp.raid_id
                WHERE r.status = 'active'
                  AND rp.state = 'searching'
                  AND rp.is_in_combat = false
                GROUP BY rp.point_id
                HAVING COUNT(*) >= 2
                """
            )
        )
        for g in groups.mappings().all():
            point_id = int(g["point_id"])
            n = int(g["n"])
            chance = min(0.05 + 0.07 * (n - 1), 0.35)
            if _RNG.random() > chance:
                continue
            await self._start_fight(point_id, now)

    async def _start_fight(self, point_id: int, now: datetime) -> None:
        pr = await self.db.execute(
            text(
                """
                SELECT rp.id AS presence_id, rp.raid_id, rp.character_id
                FROM raid_point_presence rp
                JOIN raids r ON r.id = rp.raid_id
                WHERE r.status = 'active'
                  AND rp.point_id = :point_id
                  AND rp.state = 'searching'
                  AND rp.is_in_combat = false
                ORDER BY random()
                LIMIT 2
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"point_id": point_id},
        )
        parts = pr.mappings().all()
        if len(parts) < 2:
            return

        a = parts[0]
        b = parts[1]
        a_presence_id = int(a["presence_id"])
        b_presence_id = int(b["presence_id"])
        a_raid_id = int(a["raid_id"])
        b_raid_id = int(b["raid_id"])
        a_char = int(a["character_id"])
        b_char = int(b["character_id"])

        nmq = await self.db.execute(
            text("SELECT id, name FROM characters WHERE id = ANY(:ids)"),
            {"ids": [a_char, b_char]},
        )
        names: dict[int, str] = {}
        for r in nmq.mappings().all():
            try:
                names[int(r["id"])] = str(r.get("name") or f"#{int(r['id'])}")
            except Exception:
                continue
        a_name = names.get(a_char) or f"#{a_char}"
        b_name = names.get(b_char) or f"#{b_char}"

        duration_seconds = _env_int("RAID_FIGHT_SECONDS", 300)
        ends_at = now + timedelta(seconds=duration_seconds)
        fight_raid_id = min(a_raid_id, b_raid_id)

        fr = await self.db.execute(
            text(
                """
                INSERT INTO raid_fights(raid_id, point_id, status, started_at, ended_at, duration_seconds, meta_json)
                VALUES (:raid_id, :point_id, 'active', :now, :ends_at, :dur, CAST(:meta AS jsonb))
                RETURNING id
                """
            ),
            {
                "raid_id": fight_raid_id,
                "point_id": point_id,
                "now": now,
                "ends_at": ends_at,
                "dur": duration_seconds,
                "meta": _json({"raid_ids": [a_raid_id, b_raid_id], "presence_ids": [a_presence_id, b_presence_id]}),
            },
        )
        fight_id = int(fr.scalar_one())

        await self.db.execute(
            text(
                """
                INSERT INTO raid_fight_participants(fight_id, character_id, presence_id, meta_json)
                VALUES
                  (:fight_id, :a_char, :a_presence_id, '{}'::jsonb),
                  (:fight_id, :b_char, :b_presence_id, '{}'::jsonb)
                """
            ),
            {
                "fight_id": fight_id,
                "a_char": a_char,
                "b_char": b_char,
                "a_presence_id": a_presence_id,
                "b_presence_id": b_presence_id,
            },
        )

        await self.db.execute(
            text("UPDATE raid_point_presence SET is_in_combat = true WHERE id = ANY(:ids)"),
            {"ids": [a_presence_id, b_presence_id]},
        )

        await self._log(
            a_raid_id,
            a_char,
            point_id,
            a_presence_id,
            fight_id,
            "fight_started",
            {"ends_at": ends_at.isoformat(), "opponent_id": b_char, "opponent_name": b_name, "notify": True},
        )
        await self._log(
            b_raid_id,
            b_char,
            point_id,
            b_presence_id,
            fight_id,
            "fight_started",
            {"ends_at": ends_at.isoformat(), "opponent_id": a_char, "opponent_name": a_name, "notify": True},
        )

        others = await self.db.execute(
            text(
                """
                SELECT rp.id AS presence_id, rp.raid_id, rp.character_id
                FROM raid_point_presence rp
                JOIN raids r ON r.id = rp.raid_id
                WHERE r.status = 'active'
                  AND rp.point_id = :point_id
                  AND rp.state = 'searching'
                  AND rp.is_in_combat = false
                """
            ),
            {"point_id": point_id},
        )
        for o in others.mappings().all():
            try:
                await self._log(
                    int(o["raid_id"]),
                    int(o["character_id"]),
                    point_id,
                    int(o["presence_id"]),
                    fight_id,
                    "heard_shots",
                    {"fighters": [a_name, b_name], "notify": False},
                )
            except Exception:
                continue

    async def _resolve_finished_fights(self, now: datetime) -> None:
        fights = await self.db.execute(
            text(
                """
                SELECT id, point_id, started_at
                FROM raid_fights
                WHERE status = 'active' AND ended_at IS NOT NULL AND ended_at <= :now
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now},
        )
        for fr in fights.mappings().all():
            fight_id = int(fr["id"])
            point_id = int(fr["point_id"])
            fight_started_at: datetime = fr["started_at"]

            pr = await self.db.execute(
                text(
                    """
                    SELECT p.character_id, p.presence_id, rp.raid_id, rp.search_started_at, rp.search_eta_at
                    FROM raid_fight_participants p
                    JOIN raid_point_presence rp ON rp.id = p.presence_id
                    JOIN raids r ON r.id = rp.raid_id
                    WHERE p.fight_id = :fight_id
                      AND rp.point_id = :point_id
                      AND rp.is_in_combat = true
                      AND rp.state = 'searching'
                      AND r.status = 'active'
                    """
                ),
                {"fight_id": fight_id, "point_id": point_id},
            )
            parts = pr.mappings().all()
            if len(parts) < 2:
                await self.db.execute(
                    text("UPDATE raid_fights SET status = 'canceled', ended_at = :now WHERE id = :id"),
                    {"id": fight_id, "now": now},
                )
                continue

            a = parts[0]
            b = parts[1]
            a_char = int(a["character_id"])
            b_char = int(b["character_id"])
            a_presence_id = int(a["presence_id"])
            b_presence_id = int(b["presence_id"])
            a_raid_id = int(a["raid_id"])
            b_raid_id = int(b["raid_id"])

            a_search_started_at = a.get("search_started_at")
            a_search_eta_at = a.get("search_eta_at")
            b_search_started_at = b.get("search_started_at")
            b_search_eta_at = b.get("search_eta_at")

            fight_duration_seconds = 0
            try:
                if isinstance(fight_started_at, datetime):
                    fight_duration_seconds = max(0, int((now - fight_started_at).total_seconds()))
            except Exception:
                fight_duration_seconds = 0

            bh = await self.db.execute(
                text(
                    """
                    SELECT id, behavior_model
                    FROM raids
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": [a_raid_id, b_raid_id]},
            )
            behavior_by_raid = {int(r["id"]): str(r.get("behavior_model") or "aggressive") for r in bh.mappings().all()}

            def _norm_behavior(val: str | None) -> str:
                v = str(val or "aggressive")
                return v if v in {"aggressive", "stealth"} else "aggressive"

            a_behavior = _norm_behavior(behavior_by_raid.get(a_raid_id))
            b_behavior = _norm_behavior(behavior_by_raid.get(b_raid_id))

            a_state = await self._load_combatant_state(a_char, a_behavior)
            b_state = await self._load_combatant_state(b_char, b_behavior)
            if a_state is None or b_state is None:
                await self._end_fight_as_canceled(fight_id, now, [a_presence_id, b_presence_id])
                continue

            a_no_ammo = await self._consume_battle_ammo(
                a_char, raid_id=a_raid_id, point_id=point_id, presence_id=a_presence_id, fight_id=fight_id
            )
            b_no_ammo = await self._consume_battle_ammo(
                b_char, raid_id=b_raid_id, point_id=point_id, presence_id=b_presence_id, fight_id=fight_id
            )

            if a_no_ammo:
                b_state, a_state = await self._apply_guaranteed_shots(attacker=b_state, target=a_state)
            if b_no_ammo:
                a_state, b_state = await self._apply_guaranteed_shots(attacker=a_state, target=b_state)

            res = simulate_clash_round(a_state, b_state, max_rounds=6, rng=_RNG)
            a_after = res.a_end
            b_after = res.b_end

            a_dead = int(a_after.hp_current) <= 0
            b_dead = int(b_after.hp_current) <= 0

            outcome = "draw"
            winner_char_id: int | None = None
            loser_char_id: int | None = None
            winner_raid_id: int | None = None
            loser_raid_id: int | None = None

            if a_dead and not b_dead:
                outcome = "kill"
                winner_char_id, loser_char_id = b_char, a_char
                winner_raid_id, loser_raid_id = b_raid_id, a_raid_id
            elif b_dead and not a_dead:
                outcome = "kill"
                winner_char_id, loser_char_id = a_char, b_char
                winner_raid_id, loser_raid_id = a_raid_id, b_raid_id
            elif a_dead and b_dead:
                outcome = "mutual_kill"

            await self._upsert_character_health(a_char, a_after)
            await self._upsert_character_health(b_char, b_after)

            fight_lines: list[str] = []
            try:
                for ev in res.events:
                    for ln in list(ev.log_lines or []):
                        fight_lines.append(str(ln))
                        if len(fight_lines) >= 30:
                            raise StopIteration
            except StopIteration:
                pass
            except Exception:
                fight_lines = []

            fight_result = {
                "outcome": outcome,
                "a": {"character_id": a_char, "raid_id": a_raid_id, "hp": int(a_after.hp_current)},
                "b": {"character_id": b_char, "raid_id": b_raid_id, "hp": int(b_after.hp_current)},
                "winner_character_id": winner_char_id,
                "loser_character_id": loser_char_id,
                "no_ammo": {"a": bool(a_no_ammo), "b": bool(b_no_ammo)},
                "log": fight_lines,
            }

            await self.db.execute(
                text(
                    """
                    UPDATE raid_fights
                    SET status = 'finished', ended_at = :now,
                        winner_character_id = :winner, loser_character_id = :loser,
                        meta_json = jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{result}', CAST(:res AS jsonb), true)
                    WHERE id = :id
                    """
                ),
                {"id": fight_id, "now": now, "winner": winner_char_id, "loser": loser_char_id, "res": _json(fight_result)},
            )

            await self.db.execute(
                text("UPDATE raid_point_presence SET is_in_combat = false WHERE id = ANY(:ids)"),
                {"ids": [a_presence_id, b_presence_id]},
            )

            async def _pause_search(presence_id: int, started_at: datetime | None, eta_at: datetime | None) -> None:
                if fight_duration_seconds <= 0:
                    return
                if not isinstance(started_at, datetime) or not isinstance(eta_at, datetime):
                    return

                planned = eta_at - started_at
                new_started = started_at + timedelta(seconds=fight_duration_seconds)
                new_eta = eta_at + timedelta(seconds=fight_duration_seconds)
                if new_eta <= now:
                    new_eta = now + timedelta(seconds=1)
                    if planned.total_seconds() > 0:
                        new_started = new_eta - planned

                await self.db.execute(
                    text(
                        """
                        UPDATE raid_point_presence
                        SET search_started_at = :s, search_eta_at = :e
                        WHERE id = :id AND state = 'searching'
                        """
                    ),
                    {"id": presence_id, "s": new_started, "e": new_eta},
                )

            if outcome == "kill" and winner_char_id is not None and loser_char_id is not None:
                if winner_char_id == a_char:
                    await _pause_search(a_presence_id, a_search_started_at, a_search_eta_at)
                else:
                    await _pause_search(b_presence_id, b_search_started_at, b_search_eta_at)

                await self._corpse_loot(int(winner_raid_id), int(winner_char_id), int(loser_raid_id), int(loser_char_id))
                await self._mark_raid_dead(int(loser_raid_id), int(loser_char_id), now)

            elif outcome == "mutual_kill":
                await self._mark_raid_dead(a_raid_id, a_char, now)
                await self._mark_raid_dead(b_raid_id, b_char, now)

            else:
                await _pause_search(a_presence_id, a_search_started_at, a_search_eta_at)
                await _pause_search(b_presence_id, b_search_started_at, b_search_eta_at)

            await self._log(
                a_raid_id,
                a_char,
                point_id,
                None,
                fight_id,
                "fight_finished",
                {
                    "outcome": outcome,
                    "winner": winner_char_id,
                    "loser": loser_char_id,
                    "hp": {"a": int(a_after.hp_current), "b": int(b_after.hp_current)},
                    "log": fight_lines[:25],
                    "notify": False,
                },
            )
            await self._log(
                b_raid_id,
                b_char,
                point_id,
                None,
                fight_id,
                {
                    "outcome": outcome,
                    "winner": winner_char_id,
                    "loser": loser_char_id,
                    "hp": {"a": int(a_after.hp_current), "b": int(b_after.hp_current)},
                    "log": fight_lines[:25],
                    "notify": False,
                }["outcome"] and "fight_finished",
                {
                    "outcome": outcome,
                    "winner": winner_char_id,
                    "loser": loser_char_id,
                    "hp": {"a": int(a_after.hp_current), "b": int(b_after.hp_current)},
                    "log": fight_lines[:25],
                    "notify": False,
                },
            )

    async def _end_fight_as_canceled(self, fight_id: int, now: datetime, presence_ids: list[int]) -> None:
        await self.db.execute(
            text("UPDATE raid_fights SET status = 'canceled', ended_at = :now WHERE id = :id"),
            {"id": fight_id, "now": now},
        )
        if presence_ids:
            await self.db.execute(
                text("UPDATE raid_point_presence SET is_in_combat = false WHERE id = ANY(:ids)"),
                {"ids": presence_ids},
            )

    async def _finish_searches(self, now: datetime) -> None:
        rows = await self.db.execute(
            text(
                """
                SELECT rp.id AS presence_id, rp.raid_id, rp.character_id, rp.point_id,
                       rp.search_started_at, rp.search_eta_at,
                       r.search_minutes_spent, r.search_limit_minutes, r.location_id, r.search_goal
                FROM raid_point_presence rp
                JOIN raids r ON r.id = rp.raid_id
                WHERE r.status = 'active'
                  AND rp.state = 'searching'
                  AND rp.is_in_combat = false
                  AND rp.search_eta_at IS NOT NULL
                  AND rp.search_eta_at <= :now
                ORDER BY random()
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now},
        )
        for p in rows.mappings().all():
            presence_id = int(p["presence_id"])
            raid_id = int(p["raid_id"])
            character_id = int(p["character_id"])
            point_id = int(p["point_id"])
            started_at = p["search_started_at"]
            eta_at = p["search_eta_at"]

            spent = int(p["search_minutes_spent"] or 0)
            limit = int(p["search_limit_minutes"] or 0)
            location_id = int(p["location_id"])
            goal = str(p["search_goal"] or "any")

            minutes_here = _minutes_between(started_at, eta_at)

            spent_new = min(limit, spent + minutes_here)
            await self.db.execute(
                text("UPDATE raids SET search_minutes_spent = :s WHERE id = :id"),
                {"id": raid_id, "s": spent_new},
            )

            await self._ensure_point_loot(point_id)
            looted = await self._award_loot_from_point(raid_id, character_id, point_id, minutes_here)

            await self.db.execute(
                text("UPDATE raid_point_presence SET state = 'left', left_at = :now WHERE id = :id"),
                {"id": presence_id, "now": now},
            )
            await self._log(
                raid_id,
                character_id,
                point_id,
                presence_id,
                None,
                "search_finished",
                {"minutes_here": minutes_here, "looted": looted},
            )

            if spent_new >= limit:
                await self._start_exit(raid_id, character_id, now)
                continue

            next_point = await self._pick_next_point(raid_id, location_id, goal)
            if next_point is None:
                await self._start_exit(raid_id, character_id, now)
                continue

            await self._start_travel_to_point(raid_id, character_id, next_point, now)

    async def _start_exit(self, raid_id: int, character_id: int, now: datetime) -> None:
        exit_eta = now + timedelta(minutes=_rand_minutes(15, 30))
        await self.db.execute(
            text(
                """
                UPDATE raids
                SET phase = 'exiting',
                    meta_json = jsonb_set(
                      jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{exit_started_at}', to_jsonb(CAST(:s AS text)), true),
                      '{exit_eta_at}', to_jsonb(CAST(:e AS text)), true
                    )
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": raid_id, "s": now.isoformat(), "e": exit_eta.isoformat()},
        )
        await self._log(raid_id, character_id, None, None, None, "exit_started", {"exit_eta_at": exit_eta.isoformat()})

    async def _finish_exits(self, now: datetime) -> None:
        rows = await self.db.execute(
            text(
                """
                SELECT id, character_id
                FROM raids
                WHERE status = 'active'
                  AND phase = 'exiting'
                  AND (meta_json->>'exit_eta_at') IS NOT NULL
                  AND (meta_json->>'exit_eta_at')::timestamptz <= :now
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"now": now},
        )
        for r in rows.mappings().all():
            await self._finalize_raid(int(r["id"]), int(r["character_id"]), now)

    async def _finalize_raid(self, raid_id: int, character_id: int, now: datetime) -> None:
        started = await self.db.execute(text("SELECT started_at FROM raids WHERE id = :id"), {"id": raid_id})
        started_at = started.scalar()
        duration_seconds = 0
        if isinstance(started_at, datetime):
            try:
                duration_seconds = max(0, int((now - started_at).total_seconds()))
            except Exception:
                duration_seconds = 0

        inv = await self.db.execute(text("SELECT item_id, qty FROM raid_inventory WHERE raid_id = :r"), {"r": raid_id})
        loot_rows = inv.mappings().all()

        loot: list[dict] = []
        for row in loot_rows:
            try:
                loot.append({"item_id": int(row["item_id"]), "qty": int(row["qty"])})
            except Exception:
                continue

            await self.db.execute(
                text(
                    """
                    INSERT INTO character_inventory(character_id, item_id, qty)
                    VALUES (:cid, :item_id, :qty)
                    ON CONFLICT (character_id, item_id)
                    DO UPDATE SET qty = character_inventory.qty + EXCLUDED.qty
                    """
                ),
                {"cid": character_id, "item_id": int(row["item_id"]), "qty": int(row["qty"])},
            )

        await self.db.execute(text("DELETE FROM raid_inventory WHERE raid_id = :r"), {"r": raid_id})

        result = {"outcome": "finished", "ended_at": now.isoformat(), "duration_seconds": int(duration_seconds), "loot": loot}

        await self.db.execute(
            text(
                """
                UPDATE raids
                SET status = 'finished',
                    ended_at = :now,
                    meta_json = jsonb_set(
                      jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{result}', CAST(:res AS jsonb), true),
                      '{summary_shown}', 'false'::jsonb, true
                    )
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": raid_id, "now": now, "res": _json(result)},
        )

        await self._log(raid_id, character_id, None, None, None, "raid_finished", {})

    async def _mark_raid_dead(self, raid_id: int, character_id: int, now: datetime) -> None:
        started = await self.db.execute(text("SELECT started_at FROM raids WHERE id = :id"), {"id": raid_id})
        started_at = started.scalar()
        duration_seconds = 0
        if isinstance(started_at, datetime):
            try:
                duration_seconds = max(0, int((now - started_at).total_seconds()))
            except Exception:
                duration_seconds = 0

        # On death – equipped weapons must be lost, not returned to storage.
        eq = await self.db.execute(
            text(
                """
                SELECT weapon_1_id, weapon_2_id, weapon_3_id
                FROM equipment
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id)},
        )
        e = eq.mappings().first() or {}

        weapon_ids: list[int] = []
        for slot in (1, 2, 3):
            wid = e.get(f"weapon_{slot}_id")
            if wid is None:
                continue
            try:
                weapon_ids.append(int(wid))
            except Exception:
                continue

        if weapon_ids:
            await self.db.execute(
                text(
                    """
                    UPDATE equipment
                    SET weapon_1_id = NULL,
                        weapon_2_id = NULL,
                        weapon_3_id = NULL
                    WHERE character_id = :cid
                    """
                ),
                {"cid": int(character_id)},
            )
            # If weapons are stored as items in inventory – remove them too.
            await self.db.execute(
                text(
                    """
                    DELETE FROM character_inventory
                    WHERE character_id = :cid AND item_id = ANY(:ids)
                    """
                ),
                {"cid": int(character_id), "ids": weapon_ids},
            )

        inv = await self.db.execute(text("SELECT item_id, qty FROM raid_inventory WHERE raid_id = :r"), {"r": raid_id})
        lost_rows = inv.mappings().all()

        loot_lost: list[dict] = []
        for row in lost_rows:
            try:
                loot_lost.append({"item_id": int(row["item_id"]), "qty": int(row["qty"])})
            except Exception:
                continue

        await self.db.execute(text("DELETE FROM raid_inventory WHERE raid_id = :r"), {"r": raid_id})

        result = {"outcome": "dead", "ended_at": now.isoformat(), "duration_seconds": int(duration_seconds), "loot_lost": loot_lost}

        await self.db.execute(
            text(
                """
                UPDATE raids
                SET status = 'dead',
                    ended_at = :now,
                    meta_json = jsonb_set(
                      jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{result}', CAST(:res AS jsonb), true),
                      '{summary_shown}', 'false'::jsonb, true
                    )
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": raid_id, "now": now, "res": _json(result)},
        )

        await self.db.execute(
            text(
                """
                UPDATE raid_point_presence
                SET state = 'left', left_at = :now, is_in_combat = false
                WHERE raid_id = :rid AND character_id = :cid AND state IN ('traveling','searching')
                """
            ),
            {"rid": raid_id, "cid": character_id, "now": now},
        )
        await self._log(raid_id, character_id, None, None, None, "raid_dead", {})

    async def _pick_next_point(self, raid_id: int, location_id: int, goal: str) -> int | None:
        rows = await self.db.execute(
            text(
                """
                SELECT p.id, p.base_weight, COALESCE(w.weight, 1) AS wt
                FROM raid_points p
                LEFT JOIN raid_point_itemtype_weights w
                  ON w.point_id = p.id AND w.item_type = :goal
                WHERE p.location_id = :lid
                  AND NOT EXISTS (
                    SELECT 1 FROM raid_visited_points vp
                    WHERE vp.raid_id = :rid AND vp.point_id = p.id
                  )
                """
            ),
            {"rid": raid_id, "lid": location_id, "goal": goal},
        )
        pts = rows.mappings().all()
        if not pts:
            return None

        ids = [int(x["id"]) for x in pts]
        weights = [max(0, int(x["base_weight"] or 1)) * max(0, int(x["wt"] or 1)) for x in pts]
        if sum(weights) <= 0:
            return _RNG.choice(ids)
        return _RNG.choices(ids, weights=weights, k=1)[0]

    async def _start_travel_to_point(self, raid_id: int, character_id: int, point_id: int, now: datetime) -> None:
        max_seq = await self.db.execute(
            text("SELECT COALESCE(MAX(seq_no), 0) FROM raid_visited_points WHERE raid_id = :r"),
            {"r": raid_id},
        )
        seq = int(max_seq.scalar() or 0) + 1

        await self.db.execute(
            text(
                """
                INSERT INTO raid_visited_points(raid_id, point_id, seq_no, visited_at)
                VALUES (:rid, :pid, :seq, :now)
                ON CONFLICT (raid_id, point_id) DO NOTHING
                """
            ),
            {"rid": raid_id, "pid": point_id, "seq": seq, "now": now},
        )

        ptn = await self.db.execute(text("SELECT name FROM raid_points WHERE id = :pid"), {"pid": point_id})
        point_name = str(ptn.scalar() or f"Точка {int(point_id)}")

        eta = _travel_eta(now)
        await self.db.execute(
            text(
                """
                INSERT INTO raid_point_presence(raid_id, character_id, point_id, state, travel_started_at, travel_eta_at, meta_json)
                VALUES (:rid, :cid, :pid, 'traveling', :now, :eta, '{}'::jsonb)
                ON CONFLICT (raid_id, character_id, point_id) DO NOTHING
                """
            ),
            {"rid": raid_id, "cid": character_id, "pid": point_id, "now": now, "eta": eta},
        )

        await self.db.execute(
            text("UPDATE raids SET phase = 'traveling', current_point_id = :p WHERE id = :r"),
            {"r": raid_id, "p": point_id},
        )

        await self._log(
            raid_id,
            character_id,
            point_id,
            None,
            None,
            "travel_started",
            {"travel_eta_at": eta.isoformat(), "point_name": point_name, "notify": False},
        )

    async def _ensure_point_loot(self, point_id: int) -> None:
        c = await self.db.execute(text("SELECT COUNT(*) FROM raid_point_loot WHERE point_id = :p"), {"p": point_id})
        count = int(c.scalar() or 0)
        if count >= 6:
            return

        need = 6 - count

        items = await self.db.execute(
            text(
                """
                SELECT id
                FROM items
                WHERE price >= 0
                ORDER BY random()
                LIMIT :n
                """
            ),
            {"n": need},
        )
        rows = items.mappings().all()

        if not rows:
            items2 = await self.db.execute(
                text(
                    """
                    SELECT id
                    FROM items
                    ORDER BY random()
                    LIMIT :n
                    """
                ),
                {"n": need},
            )
            rows = items2.mappings().all()

        for r in rows:
            item_id = int(r["id"])
            now2 = _utcnow()
            await self.db.execute(
                text(
                    """
                    INSERT INTO raid_point_loot(point_id, item_id, qty, spawned_at, updated_at, meta_json)
                    VALUES (:p, :i, 1, :now, :now, '{}'::jsonb)
                    ON CONFLICT (point_id, item_id)
                    DO UPDATE SET qty = raid_point_loot.qty + 1, updated_at = EXCLUDED.updated_at
                    """
                ),
                {"p": point_id, "i": item_id, "now": now2},
            )

    async def _award_loot_from_point(self, raid_id: int, character_id: int, point_id: int, minutes_here: int) -> list[dict]:
        force = _env_int("RAID_TEST_LOOT_ALWAYS", 0) == 1
        attempts = 3 if force else _RNG.randint(1, 3)
        base_p = 1.0 if force else min(0.05 + 0.04 * max(0, minutes_here), 0.9)

        ptn = await self.db.execute(text("SELECT name FROM raid_points WHERE id = :pid"), {"pid": point_id})
        point_name = str(ptn.scalar() or f"Точка {int(point_id)}")

        out: list[dict] = []

        for _ in range(attempts):
            if _RNG.random() > base_p:
                continue

            q = await self.db.execute(
                text(
                    """
                    SELECT l.item_id, l.qty, i.weight, i.name AS item_name
                    FROM raid_point_loot l
                    JOIN items i ON i.id = l.item_id
                    WHERE l.point_id = :p AND l.qty > 0
                    ORDER BY random()
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"p": point_id},
            )
            row = q.mappings().first()
            if not row:
                continue

            item_id = int(row["item_id"])
            qty_here = int(row.get("qty") or 0)
            item_weight = float(row.get("weight") or 0)
            item_name = str(row.get("item_name") or f"item#{item_id}")

            if qty_here <= 0:
                continue

            if not await self._can_take_item(character_id, raid_id, item_weight):
                continue

            now2 = _utcnow()

            if qty_here == 1:
                dec = await self.db.execute(
                    text(
                        """
                        DELETE FROM raid_point_loot
                        WHERE point_id = :p AND item_id = :i AND qty = 1
                        RETURNING 1
                        """
                    ),
                    {"p": point_id, "i": item_id},
                )
                if dec.scalar() is None:
                    continue
            else:
                dec = await self.db.execute(
                    text(
                        """
                        UPDATE raid_point_loot
                        SET qty = qty - 1, updated_at = :now
                        WHERE point_id = :p AND item_id = :i AND qty > 1
                        RETURNING qty
                        """
                    ),
                    {"p": point_id, "i": item_id, "now": now2},
                )
                if dec.scalar() is None:
                    continue

            await self.db.execute(
                text(
                    """
                    INSERT INTO raid_inventory(raid_id, item_id, qty, source_point_id, obtained_at, meta_json)
                    VALUES (:r, :i, 1, :p, :now, '{}'::jsonb)
                    ON CONFLICT (raid_id, item_id)
                    DO UPDATE SET qty = raid_inventory.qty + 1
                    """
                ),
                {"r": raid_id, "i": item_id, "p": point_id, "now": now2},
            )

            out.append({"item_id": item_id, "qty": 1})

            await self._log(
                raid_id,
                character_id,
                point_id,
                None,
                None,
                "item_found",
                {"item_id": item_id, "item_name": item_name, "qty": 1, "point_name": point_name, "notify": True},
            )

        return out

    async def _can_take_item(self, character_id: int, raid_id: int, add_weight: float) -> bool:
        r = await self.db.execute(text("SELECT carry_capacity, load FROM characters WHERE id = :cid"), {"cid": character_id})
        ch = r.mappings().first()
        if not ch:
            return False

        cap = float(ch.get("carry_capacity") or 0)
        base_load = float(ch.get("load") or 0)

        w = await self.db.execute(
            text(
                """
                SELECT COALESCE(SUM(i.weight * ri.qty), 0) AS w
                FROM raid_inventory ri
                JOIN items i ON i.id = ri.item_id
                WHERE ri.raid_id = :r
                """
            ),
            {"r": raid_id},
        )
        raid_load = float(w.scalar() or 0)

        return (base_load + raid_load + float(add_weight or 0)) <= cap

    async def _consume_battle_ammo(
        self,
        character_id: int,
        raid_id: int | None = None,
        point_id: int | None = None,
        presence_id: int | None = None,
        fight_id: int | None = None,
    ) -> bool:
        eq = await self.db.execute(
            text(
                """
                SELECT weapon_1_id, weapon_2_id, weapon_3_id
                FROM equipment
                WHERE character_id = :cid
                """
            ),
            {"cid": character_id},
        )
        row = eq.mappings().first()
        if not row:
            return True

        weapon_ids: list[int] = []
        for slot in (1, 2, 3):
            wid = row.get(f"weapon_{slot}_id")
            if wid is not None:
                try:
                    weapon_ids.append(int(wid))
                except Exception:
                    continue

        weapon_names: dict[int, str] = {}
        if weapon_ids:
            wq = await self.db.execute(text("SELECT id, name FROM weapons WHERE id = ANY(:ids)"), {"ids": weapon_ids})
            for w in wq.mappings().all():
                try:
                    weapon_names[int(w["id"])] = str(w.get("name") or f"weapon#{int(w['id'])}")
                except Exception:
                    continue

        missing = False
        for slot in (1, 2, 3):
            wid = row.get(f"weapon_{slot}_id")
            if wid is None:
                continue
            wid = int(wid)

            dec = await self.db.execute(
                text(
                    """
                    UPDATE character_ammo_loadout
                    SET qty = qty - 1
                    WHERE character_id = :cid
                      AND weapon_slot = :slot
                      AND ammo_type_id IS NOT NULL
                      AND qty >= 1
                    RETURNING qty
                    """
                ),
                {"cid": character_id, "slot": slot},
            )
            new_qty = dec.scalar()
            if new_qty is None:
                missing = True
                if raid_id is not None and point_id is not None:
                    await self._log(
                        int(raid_id),
                        int(character_id),
                        int(point_id),
                        int(presence_id) if presence_id is not None else None,
                        int(fight_id) if fight_id is not None else None,
                        "ammo_empty",
                        {"weapon_slot": int(slot), "weapon_name": weapon_names.get(wid), "notify": True},
                    )
                continue

            try:
                if int(new_qty) <= 0 and raid_id is not None and point_id is not None:
                    await self._log(
                        int(raid_id),
                        int(character_id),
                        int(point_id),
                        int(presence_id) if presence_id is not None else None,
                        int(fight_id) if fight_id is not None else None,
                        "ammo_empty",
                        {"weapon_slot": int(slot), "weapon_name": weapon_names.get(wid), "notify": True},
                    )
            except (TypeError, ValueError):
                LOG.debug("ammo qty parse failed", exc_info=True)

        return missing

    async def _apply_guaranteed_shots(self, *, attacker: CombatantState, target: CombatantState) -> tuple[CombatantState, CombatantState]:
        if not attacker.weapons:
            return attacker, target

        w = attacker.weapons[0]
        m = calc_shooting_model(
            ACCc=float(attacker.accuracy),
            ACCw=float(w.accuracy),
            RELw=float(w.reliability),
            CAT=str(w.category),
            DMG=float(w.dmg),
            AP=float(w.ap),
            REAd=float(target.reaction),
            DEFbase=float(target.defense_base_pct),
            RELarmor=float(max(0.0, min(100.0, target.rel_armor_pct))),
        )

        series = simulate_series(
            attempts=1,
            shots=2,
            p_hit=1.0,
            p_jam=0.0,
            d_hit=float(m["d_hit"]),
            category=str(w.category),
            mannequin_hp=int(target.hp_current),
            mannequin_hp_max=int(target.hp_max),
        )

        target.hp_current = max(0, int(series.get("mannequin_hp_end") or target.hp_current))
        target.injuries.add_from_dict(series.get("inj") or {})
        return attacker, target

    async def _load_combatant_state(self, character_id: int, behavior: str) -> CombatantState | None:
        ch = await self.db.execute(
            text(
                """
                SELECT c.id, c.name, c.hp AS hp_max, c.initiative, c.reaction, c.accuracy,
                       COALESCE(h.current_hp, c.hp) AS hp_current,
                       COALESCE(h.head_injury, 0) AS head_injury,
                       COALESCE(h.torso_injury, 0) AS torso_injury,
                       COALESCE(h.arm_injury, 0) AS arm_injury,
                       COALESCE(h.leg_injury, 0) AS leg_injury
                FROM characters c
                LEFT JOIN character_health h ON h.character_id = c.id
                WHERE c.id = :cid
                """
            ),
            {"cid": character_id},
        )
        c = ch.mappings().first()
        if not c:
            return None

        injuries = InjuryState(
            head=int(c["head_injury"] or 0),
            torso=int(c["torso_injury"] or 0),
            arm=int(c["arm_injury"] or 0),
            leg=int(c["leg_injury"] or 0),
        )

        eq = await self.db.execute(
            text(
                """
                SELECT head_item_id, body_item_id, gloves_item_id, boots_item_id,
                       weapon_1_id, weapon_2_id, weapon_3_id
                FROM equipment
                WHERE character_id = :cid
                """
            ),
            {"cid": character_id},
        )
        e = eq.mappings().first() or {}

        armor_ids = [e.get("head_item_id"), e.get("body_item_id"), e.get("gloves_item_id"), e.get("boots_item_id")]
        armor_ids = [int(x) for x in armor_ids if x is not None]

        armor = 0
        armor_rel_vals: list[float] = []
        if armor_ids:
            ar = await self.db.execute(
                text(
                    """
                    SELECT s.armor, s.reliability
                    FROM item_equipment_stats s
                    WHERE s.item_id = ANY(:ids)
                    """
                ),
                {"ids": armor_ids},
            )
            for r in ar.mappings().all():
                armor += int(r.get("armor") or 0)
                armor_rel_vals.append(float(r.get("reliability") or 100))

        rel_armor_pct = float(sum(armor_rel_vals) / len(armor_rel_vals)) if armor_rel_vals else 100.0
        defense_base_pct = float(max(0, min(100, int(armor))))

        beh = str(behavior or "aggressive")
        if beh not in {"aggressive", "stealth"}:
            beh = "aggressive"

        stealth = max(
            0.0,
            float(c["reaction"] or 0) * 2.0
            + (25.0 if beh == "stealth" else 0.0)
            - float(int(injuries.head) + int(injuries.torso) + int(injuries.arm) + int(injuries.leg)) * 5.0,
        )

        weapons: list[WeaponSnapshot] = []
        for slot in (1, 2, 3):
            wid = e.get(f"weapon_{slot}_id")
            if wid is None:
                continue

            wq = await self.db.execute(
                text(
                    """
                    SELECT w.name, w.category, c.code AS caliber,
                           COALESCE(w.accuracy, 0) AS acc,
                           COALESCE(w.reliability, 0) AS rel,
                           al.ammo_type_id, COALESCE(al.qty, 0) AS qty,
                           COALESCE(a.damage, 0) AS dmg, COALESCE(a.armor_penetration, 0) AS ap
                    FROM weapons w
                    JOIN calibers c ON c.id = w.caliber_id
                    LEFT JOIN character_ammo_loadout al
                      ON al.character_id = :cid AND al.weapon_slot = :slot
                    LEFT JOIN ammo_types a ON a.id = al.ammo_type_id
                    WHERE w.id = :wid
                    """
                ),
                {"cid": character_id, "slot": slot, "wid": int(wid)},
            )
            wr = wq.mappings().first()
            if not wr:
                continue

            weapons.append(
                WeaponSnapshot(
                    slot=int(slot),
                    name=str(wr.get("name") or f"weapon#{wid}"),
                    category=str(wr.get("category") or "rifle"),
                    caliber=str(wr.get("caliber") or ""),
                    accuracy=int(wr.get("acc") or 0),
                    reliability=int(wr.get("rel") or 0),
                    dmg=int(wr.get("dmg") or 0),
                    ap=int(wr.get("ap") or 0),
                )
            )

        return CombatantState(
            character_id=int(c["id"]),
            name=str(c.get("name") or f"#{character_id}"),
            hp_max=int(c["hp_max"] or 0),
            hp_current=int(c["hp_current"] or 0),
            accuracy=int(c["accuracy"] or 0),
            reaction=float(c["reaction"] or 0),
            initiative=float(c["initiative"] or 0),
            stealth=float(stealth),
            defense_base_pct=float(defense_base_pct),
            rel_armor_pct=float(max(0.0, min(100.0, rel_armor_pct))),
            weapons=weapons,
            behavior=str(beh),
            injuries=injuries,
        )

    async def _upsert_character_health(self, character_id: int, state: CombatantState) -> None:
        rem = max(0, int(state.hp_max) - int(state.hp_current))
        now = _utcnow()

        recovery_until: datetime | None = None
        if rem > 0:
            recovery_until = now + timedelta(seconds=int(rem) * 600)

        await self.db.execute(
            text(
                """
                INSERT INTO character_health(
                    character_id,
                    current_hp,
                    recovery_until,
                    head_injury,
                    torso_injury,
                    arm_injury,
                    leg_injury,
                    updated_at
                )
                VALUES (
                    :cid,
                    :hp,
                    :recovery_until,
                    :h, :t, :a, :l,
                    :now
                )
                ON CONFLICT (character_id)
                DO UPDATE SET
                    current_hp = EXCLUDED.current_hp,
                    recovery_until = EXCLUDED.recovery_until,
                    head_injury = EXCLUDED.head_injury,
                    torso_injury = EXCLUDED.torso_injury,
                    arm_injury = EXCLUDED.arm_injury,
                    leg_injury = EXCLUDED.leg_injury,
                    updated_at = EXCLUDED.updated_at;
                """
            ),
            {
                "cid": int(character_id),
                "hp": int(state.hp_current),
                "recovery_until": recovery_until,
                "h": int(state.injuries.head),
                "t": int(state.injuries.torso),
                "a": int(state.injuries.arm),
                "l": int(state.injuries.leg),
                "now": now,
            },
        )

    async def _corpse_loot(self, winner_raid_id: int, winner_char_id: int, loser_raid_id: int, loser_char_id: int) -> None:
        found_parts: list[str] = []

        victim_items = await self.db.execute(
            text("SELECT item_id, qty FROM raid_inventory WHERE raid_id = :r AND qty > 0 ORDER BY random() LIMIT 1"),
            {"r": loser_raid_id},
        )
        vi = victim_items.mappings().first()
        if vi:
            item_id = int(vi["item_id"])
            qty = int(vi["qty"])

            if qty <= 1:
                await self.db.execute(text("DELETE FROM raid_inventory WHERE raid_id = :r AND item_id = :i"), {"r": loser_raid_id, "i": item_id})
            else:
                await self.db.execute(
                    text("UPDATE raid_inventory SET qty = qty - 1 WHERE raid_id = :r AND item_id = :i AND qty >= 2"),
                    {"r": loser_raid_id, "i": item_id},
                )

            await self.db.execute(
                text(
                    """
                    INSERT INTO raid_inventory(raid_id, item_id, qty, obtained_at, meta_json)
                    VALUES (:r, :i, 1, :now, '{}'::jsonb)
                    ON CONFLICT (raid_id, item_id)
                    DO UPDATE SET qty = raid_inventory.qty + 1
                    """
                ),
                {"r": winner_raid_id, "i": item_id, "now": _utcnow()},
            )

            nm = await self.db.execute(text("SELECT name FROM items WHERE id = :id"), {"id": item_id})
            item_name = str(nm.scalar() or f"item#{item_id}")
            found_parts.append(f"{item_name} x1")

        eq = await self.db.execute(
            text("SELECT weapon_1_id, weapon_2_id, weapon_3_id FROM equipment WHERE character_id = :cid"),
            {"cid": loser_char_id},
        )
        e = eq.mappings().first()
        if e:
            slots: list[tuple[int, int]] = []
            for s in (1, 2, 3):
                wid = e.get(f"weapon_{s}_id")
                if wid is not None:
                    slots.append((s, int(wid)))

            if slots:
                slot, weapon_id = _RNG.choice(slots)

                if slot == 1:
                    await self.db.execute(
                        text("UPDATE equipment SET weapon_1_id = NULL WHERE character_id = :cid"),
                        {"cid": loser_char_id},
                    )
                elif slot == 2:
                    await self.db.execute(
                        text("UPDATE equipment SET weapon_2_id = NULL WHERE character_id = :cid"),
                        {"cid": loser_char_id},
                    )
                else:
                    await self.db.execute(
                        text("UPDATE equipment SET weapon_3_id = NULL WHERE character_id = :cid"),
                        {"cid": loser_char_id},
                    )
                await self.db.execute(
                    text(
                        """
                        INSERT INTO raid_inventory(raid_id, item_id, qty, obtained_at, meta_json)
                        VALUES (:r, :i, 1, :now, '{}'::jsonb)
                        ON CONFLICT (raid_id, item_id)
                        DO UPDATE SET qty = raid_inventory.qty + 1
                        """
                    ),
                    {"r": winner_raid_id, "i": weapon_id, "now": _utcnow()},
                )

                wn = await self.db.execute(text("SELECT name FROM weapons WHERE id = :id"), {"id": weapon_id})
                weapon_name = wn.scalar()
                if weapon_name is None:
                    wn2 = await self.db.execute(text("SELECT name FROM items WHERE id = :id"), {"id": weapon_id})
                    weapon_name = wn2.scalar()
                found_parts.append(str(weapon_name or f"weapon#{weapon_id}"))

        losern = await self.db.execute(text("SELECT name FROM characters WHERE id = :id"), {"id": loser_char_id})
        loser_name = str(losern.scalar() or f"#{loser_char_id}")

        found_line = ", ".join(found_parts) if found_parts else ""
        await self._log(
            winner_raid_id,
            winner_char_id,
            None,
            None,
            None,
            "corpse_search",
            {"victim_id": loser_char_id, "victim_name": loser_name, "found_line": found_line, "notify": True},
        )

    async def _log(
        self,
        raid_id: int | None,
        character_id: int | None,
        point_id: int | None,
        presence_id: int | None,
        fight_id: int | None,
        kind: str,
        payload: dict,
    ) -> None:
        if raid_id is None or character_id is None:
            return

        p: dict = dict(payload or {})
        if "text" not in p:
            txt = _event_text(kind, p)
            if txt:
                p["text"] = txt
        if "notify" not in p:
            p["notify"] = kind in _NOTIFY_DEFAULT_KINDS

        await self.db.execute(
            text(
                """
                INSERT INTO raid_logs(raid_id, character_id, point_id, presence_id, fight_id, kind, payload, created_at)
                VALUES (:raid_id, :character_id, :point_id, :presence_id, :fight_id, :kind, CAST(:payload AS jsonb), :now)
                """
            ),
            {
                "raid_id": raid_id,
                "character_id": character_id,
                "point_id": point_id,
                "presence_id": presence_id,
                "fight_id": fight_id,
                "kind": kind,
                "payload": _json(p),
                "now": _utcnow(),
            },
        )


async def _notify_raids(session: AsyncSession, bot: Bot) -> None:
    q = await session.execute(
        text(
            """
            SELECT
                r.id AS raid_id,
                r.character_id AS character_id,
                u.tg_id AS tg_id,
                COALESCE((r.meta_json->>'notify_last_log_id')::bigint, 0) AS last_log_id
            FROM raids r
            JOIN characters c ON c.id = r.character_id
            JOIN users u ON u.id = c.user_id
            WHERE r.status = 'active'
              AND COALESCE((r.meta_json->>'notify_enabled')::boolean, false) = true
              AND u.tg_id IS NOT NULL
            """
        )
    )
    raids = q.mappings().all()

    for r in raids:
        raid_id = int(r["raid_id"])
        character_id = int(r["character_id"])
        tg_id = int(r["tg_id"])
        last_id = int(r.get("last_log_id") or 0)

        logs_q = await session.execute(
            text(
                """
                SELECT id, kind, payload, created_at
                FROM raid_logs
                WHERE raid_id = :raid_id
                  AND character_id = :character_id
                  AND id > :last_id
                  AND COALESCE((payload->>'notify')::boolean, false) = true
                ORDER BY id ASC
                LIMIT 10
                """
            ),
            {"raid_id": raid_id, "character_id": character_id, "last_id": last_id},
        )
        rows = logs_q.mappings().all()
        if not rows:
            continue

        new_last_id = last_id
        sent = 0

        for row in rows:
            try:
                new_last_id = max(new_last_id, int(row["id"]))
            except Exception:
                continue

            kind = str(row.get("kind") or "")
            payload = row.get("payload") or {}

            text_msg = ""
            try:
                if isinstance(payload, dict):
                    text_msg = str(payload.get("text") or "")
                if not text_msg:
                    text_msg = _event_text(kind, payload if isinstance(payload, dict) else {})
            except Exception:
                text_msg = ""

            if not text_msg:
                continue

            try:
                msg = await bot.send_message(chat_id=tg_id, text=text_msg)
                asyncio.create_task(_delete_message_later(bot, tg_id, int(msg.message_id), 60))
                sent += 1
                if sent >= 5:
                    break
            except Exception:
                break

        try:
            await session.execute(
                text(
                    """
                    UPDATE raids
                    SET meta_json = COALESCE(meta_json, '{}'::jsonb)
                                   || jsonb_build_object('notify_last_log_id', CAST(:last_id AS bigint))
                    WHERE id = :rid
                    """
                ),
                {"rid": raid_id, "last_id": new_last_id},
            )
        except Exception:
            continue


async def raids_ticker(
    sessionmaker: async_sessionmaker[AsyncSession],
    tick_seconds: int = 60,
    bot: Bot | None = None,
) -> None:
    while True:
        try:
            async with sessionmaker() as session:
                async with session.begin():
                    await RaidsEngine(session).tick()
        except Exception:
            LOG.exception("raids_ticker crashed")

        if bot is not None:
            try:
                async with sessionmaker() as session2:
                    async with session2.begin():
                        await _notify_raids(session2, bot)
            except Exception:
                LOG.exception("raids_notifier crashed")

        await asyncio.sleep(tick_seconds)
