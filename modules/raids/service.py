from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import randint
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import RaidsEngine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _esc_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    mins = seconds // 60
    hrs = mins // 60
    mins = mins % 60
    if hrs > 0:
        return f"{hrs}ч {mins}м"
    return f"{mins}м"


@dataclass(frozen=True)
class StartRaidResult:
    ok: bool
    message: str
    raid_id: int | None = None


class RaidsService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def tick(self) -> None:
        eng = RaidsEngine(self.db)
        await eng.tick()
        await self.db.flush()

    def _travel_eta(self) -> datetime:
        try:
            seconds = int(os.environ.get("RAID_TRAVEL_SECONDS", "20") or "20")
        except Exception:
            seconds = 20
        if seconds <= 0:
            return _utcnow() + timedelta(minutes=randint(15, 30))
        return _utcnow() + timedelta(seconds=seconds)

    async def ensure_seed_locations(self) -> None:
        existing = await self.db.execute(text("SELECT COUNT(*) FROM raid_locations"))
        if int(existing.scalar() or 0) > 0:
            return

        locs = [
            ("loc1", "Локация 1"),
            ("loc2", "Локация 2"),
            ("loc3", "Локация 3"),
            ("loc4", "Локация 4"),
        ]

        loc_ids: list[int] = []
        for code, name in locs:
            r = await self.db.execute(
                text(
                    """
                    INSERT INTO raid_locations(code, name, is_active, tier_rules_json, meta_json)
                    VALUES (:code, :name, true, '{}'::jsonb, '{}'::jsonb)
                    RETURNING id
                    """
                ),
                {"code": code, "name": name},
            )
            loc_ids.append(int(r.scalar_one()))

        points_per_loc = [5, 6, 7, 8]
        item_types = ["head", "body", "gloves", "boots", "misc", "Weapon Upgrade", "Ammo", "weapon"]

        for idx, loc_id in enumerate(loc_ids):
            n = points_per_loc[idx]
            for p in range(1, n + 1):
                code = f"p{p}"
                name = f"Точка {p}"
                pr = await self.db.execute(
                    text(
                        """
                        INSERT INTO raid_points(location_id, code, name, base_weight, meta_json)
                        VALUES (:location_id, :code, :name, 1, '{}'::jsonb)
                        RETURNING id
                        """
                    ),
                    {"location_id": loc_id, "code": code, "name": name},
                )
                point_id = int(pr.scalar_one())

                for it in item_types:
                    await self.db.execute(
                        text(
                            """
                            INSERT INTO raid_point_itemtype_weights(point_id, item_type, weight)
                            VALUES (:point_id, :item_type, 1)
                            """
                        ),
                        {"point_id": point_id, "item_type": it},
                    )

    async def list_locations(self) -> list[dict]:
        await self.ensure_seed_locations()
        q = await self.db.execute(text("SELECT id, code, name FROM raid_locations WHERE is_active = true ORDER BY id"))
        return [dict(r) for r in q.mappings().all()]

    async def get_location(self, location_id: int) -> dict | None:
        q = await self.db.execute(
            text("SELECT id, code, name FROM raid_locations WHERE id = :id AND is_active = true"),
            {"id": int(location_id)},
        )
        row = q.mappings().first()
        return dict(row) if row else None

    async def get_character_gate_error(self, tg_id: int, character_id: int) -> str | None:
        q = await self.db.execute(
            text(
                """
                SELECT c.id, c.hp AS hp_max, COALESCE(h.current_hp, c.hp) AS hp_cur,
                       e.weapon_1_id, e.weapon_2_id, e.weapon_3_id
                FROM characters c
                JOIN users u ON u.id = c.user_id
                LEFT JOIN character_health h ON h.character_id = c.id
                LEFT JOIN equipment e ON e.character_id = c.id
                WHERE u.tg_id = :tg_id AND c.id = :cid
                """
            ),
            {"tg_id": int(tg_id), "cid": int(character_id)},
        )
        row = q.mappings().first()
        if not row:
            return "Персонаж не найден."

        if int(row["hp_cur"] or 0) < int(row["hp_max"] or 0):
            return "Персонаж ранен. Нужен полный HP."

        weapons = [
            (1, row.get("weapon_1_id")),
            (2, row.get("weapon_2_id")),
            (3, row.get("weapon_3_id")),
        ]
        equipped_slots = [s for s, wid in weapons if wid is not None]
        if not equipped_slots:
            return "Нужно экипировать хотя бы одно оружие."

        a = await self.db.execute(
            text(
                """
                SELECT weapon_slot, ammo_type_id, qty
                FROM character_ammo_loadout
                WHERE character_id = :cid
                """
            ),
            {"cid": int(character_id)},
        )
        ammo = {int(r["weapon_slot"]): r for r in a.mappings().all()}

        for slot in equipped_slots:
            r2 = ammo.get(int(slot))
            if not r2:
                return "На каждое экипированное оружие нужны патроны."
            if r2.get("ammo_type_id") is None or int(r2.get("qty") or 0) <= 0:
                return "На каждое экипированное оружие нужны патроны."

        active = await self.db.execute(
            text("SELECT 1 FROM raids WHERE character_id = :cid AND status = 'active' LIMIT 1"),
            {"cid": int(character_id)},
        )
        if active.scalar() is not None:
            return "Персонаж уже в рейде."

        return None

    async def start_raid(
        self,
        tg_id: int,
        character_id: int,
        location_id: int,
        behavior_model: str,
        search_goal: str,
    ) -> StartRaidResult:
        err = await self.get_character_gate_error(tg_id, character_id)
        if err:
            return StartRaidResult(ok=False, message=err)

        loc = await self.get_location(location_id)
        if not loc:
            return StartRaidResult(ok=False, message="Локация не найдена.")

        p = await self.db.execute(
            text(
                """
                SELECT id
                FROM raid_points
                WHERE location_id = :lid
                ORDER BY random()
                LIMIT 1
                """
            ),
            {"lid": int(location_id)},
        )
        first_point_id = p.scalar()
        if first_point_id is None:
            return StartRaidResult(ok=False, message="Нет точек на локации.")
        first_point_id = int(first_point_id)

        beh = str(behavior_model or "aggressive")
        if beh not in {"aggressive", "stealth"}:
            beh = "aggressive"

        goal = str(search_goal or "any")

        r = await self.db.execute(
            text(
                """
                INSERT INTO raids(character_id, location_id, status, phase, behavior_model, search_goal, current_point_id,
                                 started_at, search_limit_minutes, search_minutes_spent, meta_json)
                VALUES (:cid, :lid, 'active', 'traveling', :beh, :goal, :pid, :now, 60, 0, '{}'::jsonb)
                RETURNING id
                """
            ),
            {
                "cid": int(character_id),
                "lid": int(location_id),
                "beh": beh,
                "goal": goal,
                "pid": int(first_point_id),
                "now": _utcnow(),
            },
        )
        raid_id = int(r.scalar_one())

        await self.db.execute(
            text(
                """
                INSERT INTO raid_visited_points(raid_id, point_id, seq_no, visited_at)
                VALUES (:rid, :pid, 1, :now)
                """
            ),
            {"rid": raid_id, "pid": first_point_id, "now": _utcnow()},
        )

        travel_eta = self._travel_eta()
        await self.db.execute(
            text(
                """
                INSERT INTO raid_point_presence(raid_id, character_id, point_id, state, travel_started_at, travel_eta_at, meta_json)
                VALUES (:rid, :cid, :pid, 'traveling', :now, :eta, '{}'::jsonb)
                """
            ),
            {"rid": raid_id, "cid": int(character_id), "pid": first_point_id, "now": _utcnow(), "eta": travel_eta},
        )

        return StartRaidResult(ok=True, message="Рейд начат.", raid_id=raid_id)

    async def _last_loot_line(self, raid_id: int) -> str:
        q = await self.db.execute(
            text(
                """
                SELECT payload, created_at
                FROM raid_logs
                WHERE raid_id = :rid AND kind = 'search_finished'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"rid": int(raid_id)},
        )
        row = q.mappings().first()
        if not row:
            return ""

        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        if not isinstance(payload, dict):
            return ""

        looted = payload.get("looted")
        if not isinstance(looted, list):
            looted = []

        if not looted:
            return "\nЛут: ничего"

        id_to_qty: dict[int, int] = {}
        for it in looted:
            if not isinstance(it, dict):
                continue
            try:
                item_id = int(it.get("item_id"))
                qty = int(it.get("qty") or 1)
            except Exception:
                continue
            id_to_qty[item_id] = id_to_qty.get(item_id, 0) + max(1, qty)

        if not id_to_qty:
            return "\nЛут: ничего"

        ids = list(id_to_qty.keys())
        names: dict[int, str] = {}
        try:
            r = await self.db.execute(
                text(
                    """
                    SELECT id, name
                    FROM items
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            for x in r.mappings().all():
                try:
                    names[int(x["id"])] = str(x.get("name") or f"item#{x['id']}")
                except Exception:
                    continue
        except Exception:
            names = {}

        parts: list[str] = []
        for item_id, qty in id_to_qty.items():
            nm = names.get(item_id) or f"item#{item_id}"
            parts.append(f"{_esc_html(nm)} x{qty}")

        return "\nЛут: " + ", ".join(parts)

    async def _items_line_from_list(self, items_list: list[dict]) -> str:
        id_to_qty: dict[int, int] = {}
        for it in items_list:
            if not isinstance(it, dict):
                continue
            try:
                item_id = int(it.get("item_id"))
                qty = int(it.get("qty") or 0)
            except Exception:
                continue
            if qty <= 0:
                continue
            id_to_qty[item_id] = id_to_qty.get(item_id, 0) + qty

        if not id_to_qty:
            return "ничего"

        ids = list(id_to_qty.keys())
        names: dict[int, str] = {}
        try:
            r = await self.db.execute(
                text(
                    """
                    SELECT id, name
                    FROM items
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            for x in r.mappings().all():
                try:
                    names[int(x["id"])] = str(x.get("name") or f"item#{x['id']}")
                except Exception:
                    continue
        except Exception:
            names = {}

        parts: list[str] = []
        for item_id, qty in id_to_qty.items():
            nm = names.get(item_id) or f"item#{item_id}"
            parts.append(f"{_esc_html(nm)} x{qty}")
        return ", ".join(parts)

    async def _maybe_last_raid_result_text(self, tg_id: int, character_id: int) -> str | None:
        q = await self.db.execute(
            text(
                """
                SELECT r.id AS raid_id, r.status, r.started_at, r.ended_at,
                       r.search_minutes_spent, r.search_limit_minutes,
                       r.behavior_model, r.search_goal,
                       r.location_id, rl.name AS location_name,
                       COALESCE((r.meta_json->>'summary_shown')::boolean, false) AS summary_shown,
                       r.meta_json
                FROM raids r
                JOIN characters c ON c.id = r.character_id
                JOIN users u ON u.id = c.user_id
                JOIN raid_locations rl ON rl.id = r.location_id
                WHERE u.tg_id = :tg_id
                  AND r.character_id = :cid
                  AND r.status IN ('finished','dead','canceled')
                  AND COALESCE((r.meta_json->>'summary_shown')::boolean, false) = false
                ORDER BY r.ended_at DESC NULLS LAST, r.started_at DESC
                LIMIT 1
                """
            ),
            {"tg_id": int(tg_id), "cid": int(character_id)},
        )
        row = q.mappings().first()
        if not row:
            return None

        raid_id = int(row["raid_id"])
        status = str(row.get("status") or "")
        started_at = row.get("started_at")
        ended_at = row.get("ended_at")

        duration_seconds = 0
        if isinstance(started_at, datetime) and isinstance(ended_at, datetime):
            try:
                duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
            except Exception:
                duration_seconds = 0

        meta = row.get("meta_json") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}

        result = meta.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {}
        if not isinstance(result, dict):
            result = {}

        loot = result.get("loot")
        loot_lost = result.get("loot_lost")
        if not isinstance(loot, list):
            loot = []
        if not isinstance(loot_lost, list):
            loot_lost = []

        fights_q = await self.db.execute(
            text(
                """
                SELECT COUNT(DISTINCT p.fight_id) AS n
                FROM raid_fight_participants p
                JOIN raid_point_presence rp ON rp.id = p.presence_id
                WHERE rp.raid_id = :rid
                """
            ),
            {"rid": raid_id},
        )
        fights_count = int(fights_q.scalar() or 0)

        kills_q = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM raid_fights f
                WHERE f.status = 'finished'
                  AND f.winner_character_id = :cid
                  AND EXISTS (
                    SELECT 1
                    FROM raid_fight_participants p
                    JOIN raid_point_presence rp ON rp.id = p.presence_id
                    WHERE p.fight_id = f.id AND rp.raid_id = :rid
                  )
                """
            ),
            {"rid": raid_id, "cid": int(character_id)},
        )
        kills_count = int(kills_q.scalar() or 0)

        enemies_q = await self.db.execute(
            text(
                """
                SELECT COUNT(DISTINCT p2.character_id) AS n
                FROM raid_fight_participants p1
                JOIN raid_fight_participants p2 ON p2.fight_id = p1.fight_id AND p2.character_id <> p1.character_id
                JOIN raid_point_presence rp1 ON rp1.id = p1.presence_id
                WHERE rp1.raid_id = :rid
                """
            ),
            {"rid": raid_id},
        )
        enemies_count = int(enemies_q.scalar() or 0)

        points_q = await self.db.execute(
            text("SELECT COUNT(*) FROM raid_visited_points WHERE raid_id = :rid"),
            {"rid": raid_id},
        )
        points_count = int(points_q.scalar() or 0)

        hp_q = await self.db.execute(
            text(
                """
                SELECT c.hp AS hp_max, COALESCE(h.current_hp, c.hp) AS hp_cur
                FROM characters c
                LEFT JOIN character_health h ON h.character_id = c.id
                WHERE c.id = :cid
                """
            ),
            {"cid": int(character_id)},
        )
        hp_row = hp_q.mappings().first() or {}
        hp_max = int(hp_row.get("hp_max") or 0)
        hp_cur = int(hp_row.get("hp_cur") or 0)

        alive_now_q = await self.db.execute(
            text("SELECT COUNT(*) FROM raids WHERE status = 'active' AND location_id = :lid"),
            {"lid": int(row.get("location_id") or 0)},
        )
        alive_now = int(alive_now_q.scalar() or 0)

        loc = _esc_html(str(row.get("location_name") or ""))
        goal = _esc_html(str(row.get("search_goal") or ""))
        beh = _esc_html(str(row.get("behavior_model") or ""))

        spent = int(row.get("search_minutes_spent") or 0)
        limit = int(row.get("search_limit_minutes") or 0)

        if status == "finished":
            outcome = "успешно"
            loot_line = await self._items_line_from_list(loot)
            loot_block = f"Добыча: {loot_line}"
        elif status == "dead":
            outcome = "поражение"
            lost_line = await self._items_line_from_list(loot_lost)
            loot_block = f"Потеряно: {lost_line}"
        else:
            outcome = "отменен"
            loot_line = await self._items_line_from_list(loot)
            loot_block = f"Добыча: {loot_line}"

        report = (
            "<b>Результат рейда</b>\n"
            f"Локация: {loc}\n"
            f"Исход: {outcome}\n"
            f"Поведение: {beh}\n"
            f"Цель: {goal}\n"
            f"Время: {_fmt_duration(duration_seconds)}\n"
            f"Поиск: {spent}/{limit} мин\n"
            f"Точек посещено: {points_count}\n"
            f"Боев: {fights_count}\n"
            f"Убито: {kills_count}\n"
            f"Противников в боях: {enemies_count}\n"
            f"HP: {hp_cur}/{hp_max}\n"
            f"Живых игроков на локации сейчас: {alive_now}\n"
            f"{loot_block}"
        )

        await self.db.execute(
            text(
                """
                UPDATE raids
                SET meta_json = jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{summary_shown}', 'true'::jsonb, true)
                WHERE id = :rid
                """
            ),
            {"rid": raid_id},
        )

        return report

    async def raid_status_text(self, tg_id: int, character_id: int) -> str:
        try:
            await self.tick()
        except Exception:
            await self.db.rollback()

        q = await self.db.execute(
            text(
                """
                SELECT r.id AS raid_id, r.status, r.phase, r.behavior_model, r.search_goal,
                       r.search_minutes_spent, r.search_limit_minutes,
                       rl.name AS location_name, rp.name AS point_name,
                       pr.state AS presence_state,
                       pr.travel_eta_at, pr.search_eta_at
                FROM raids r
                JOIN characters c ON c.id = r.character_id
                JOIN users u ON u.id = c.user_id
                JOIN raid_locations rl ON rl.id = r.location_id
                LEFT JOIN raid_points rp ON rp.id = r.current_point_id
                LEFT JOIN raid_point_presence pr
                  ON pr.raid_id = r.id AND pr.point_id = r.current_point_id AND pr.character_id = r.character_id
                WHERE u.tg_id = :tg_id AND r.character_id = :cid AND r.status = 'active'
                ORDER BY r.started_at DESC
                LIMIT 1
                """
            ),
            {"tg_id": int(tg_id), "cid": int(character_id)},
        )
        row = q.mappings().first()
        if not row:
            report = await self._maybe_last_raid_result_text(tg_id, character_id)
            if report:
                return report
            return "<b>Рейды</b>\nНет активного рейда."

        now = _utcnow()
        eta_line = ""
        if row.get("presence_state") == "traveling" and row.get("travel_eta_at"):
            eta = row["travel_eta_at"]
            secs = int((eta - now).total_seconds())
            if 0 < secs < 60:
                eta_line = f"\nПереход: ~{secs} сек"
            else:
                mins = max(0, int(secs // 60))
                eta_line = f"\nПереход: ~{mins} мин"
        if row.get("presence_state") == "searching" and row.get("search_eta_at"):
            eta = row["search_eta_at"]
            secs = int((eta - now).total_seconds())
            if 0 < secs < 60:
                eta_line = f"\nПоиск: ~{secs} сек"
            else:
                mins = max(0, int(secs // 60))
                eta_line = f"\nПоиск: ~{mins} мин"

        loc = _esc_html(str(row.get("location_name") or ""))
        pt = _esc_html(str(row.get("point_name") or ""))

        spent = int(row.get("search_minutes_spent") or 0)
        limit = int(row.get("search_limit_minutes") or 0)

        loot_line = await self._last_loot_line(int(row.get("raid_id") or 0))

        return (
            "<b>Рейд</b>\n"
            f"Локация: {loc}\n"
            f"Точка: {pt}\n"
            f"Фаза: {_esc_html(str(row.get('phase') or ''))}\n"
            f"Поведение: {_esc_html(str(row.get('behavior_model') or ''))}\n"
            f"Цель: {_esc_html(str(row.get('search_goal') or ''))}\n"
            f"Поиск: {spent}/{limit} мин"
            f"{eta_line}"
            f"{loot_line}"
        )

    async def cancel_raid(self, tg_id: int, character_id: int) -> tuple[bool, str]:
        q = await self.db.execute(
            text(
                """
                SELECT r.id AS raid_id
                FROM raids r
                JOIN characters c ON c.id = r.character_id
                JOIN users u ON u.id = c.user_id
                WHERE u.tg_id = :tg_id AND r.character_id = :cid AND r.status = 'active'
                ORDER BY r.started_at DESC
                LIMIT 1
                """
            ),
            {"tg_id": int(tg_id), "cid": int(character_id)},
        )
        row = q.mappings().first()
        if not row:
            return False, "Нет активного рейда."

        raid_id = int(row["raid_id"])

        inv = await self.db.execute(text("SELECT item_id, qty FROM raid_inventory WHERE raid_id = :rid"), {"rid": raid_id})
        inv_rows = inv.mappings().all()

        loot: list[dict] = []
        for it in inv_rows:
            try:
                loot.append({"item_id": int(it["item_id"]), "qty": int(it["qty"])})
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
                {"cid": int(character_id), "item_id": int(it["item_id"]), "qty": int(it["qty"])},
            )

        await self.db.execute(text("DELETE FROM raid_inventory WHERE raid_id = :rid"), {"rid": raid_id})

        result = {
            "outcome": "canceled",
            "ended_at": _utcnow().isoformat(),
            "loot": loot,
        }

        await self.db.execute(
            text(
                """
                UPDATE raids
                SET status = 'canceled',
                    ended_at = :now,
                    meta_json = jsonb_set(
                      jsonb_set(COALESCE(meta_json, '{}'::jsonb), '{result}', CAST(:res AS jsonb), true),
                      '{summary_shown}', 'false'::jsonb, true
                    )
                WHERE id = :rid AND status = 'active'
                """
            ),
            {"rid": raid_id, "now": _utcnow(), "res": _json(result)},
        )

        await self.db.execute(
            text(
                """
                UPDATE raid_point_presence
                SET state = 'left', left_at = :now, is_in_combat = false
                WHERE raid_id = :rid AND character_id = :cid AND state IN ('traveling','searching')
                """
            ),
            {"rid": raid_id, "cid": int(character_id), "now": _utcnow()},
        )

        return True, "Рейд отменён."
