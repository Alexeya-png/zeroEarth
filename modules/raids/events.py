from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def esc_html(s: str) -> str:
    s = s or ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse_payload(payload: Any) -> dict:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            v = json.loads(payload)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def fmt_rel_eta(eta: datetime | None, *, now: datetime | None = None) -> str:
    if not isinstance(eta, datetime):
        return ""
    now = now or utcnow()
    try:
        secs = int((eta - now).total_seconds())
    except Exception:
        return ""

    if secs <= 0:
        return ""

    if secs < 60:
        return f"~{secs} сек"

    mins = int(secs // 60)
    if mins < 60:
        return f"~{mins} мин"

    hrs = int(mins // 60)
    mins = int(mins % 60)
    return f"~{hrs}ч {mins}м"


@dataclass(frozen=True)
class RaidLogContext:
    point_names: dict[int, str]
    item_names: dict[int, str]
    char_names: dict[int, str]


class RaidEvents:
    # Kinds that can be shown as ephemeral notifications.
    NOTIFY_KINDS: set[str] = {
        "travel_started",
        "arrived",
        "heard_shots",
        "fight_started",
        "ammo_out",
        "fight_finished",
        "found_items",
        "exit_started",
        "raid_finished",
        "raid_dead",
    }

    @staticmethod
    async def build_context(db: AsyncSession, logs: list[dict]) -> RaidLogContext:
        point_ids: set[int] = set()
        item_ids: set[int] = set()
        char_ids: set[int] = set()

        for r in logs:
            try:
                if r.get("point_id") is not None:
                    point_ids.add(int(r["point_id"]))
            except Exception:
                pass

            payload = _parse_payload(r.get("payload"))

            for key in ("enemy_character_id", "winner_character_id", "loser_character_id"):
                try:
                    v = payload.get(key)
                    if v is not None:
                        char_ids.add(int(v))
                except Exception:
                    continue

            # loot lists
            for list_key in ("items", "looted", "lost", "found"):
                xs = payload.get(list_key)
                if isinstance(xs, list):
                    for it in xs:
                        if not isinstance(it, dict):
                            continue
                        try:
                            item_ids.add(int(it.get("item_id")))
                        except Exception:
                            continue

            # single items
            for single_key in ("item_id", "weapon_id"):
                try:
                    v = payload.get(single_key)
                    if v is not None:
                        item_ids.add(int(v))
                except Exception:
                    continue

        point_names: dict[int, str] = {}
        item_names: dict[int, str] = {}
        char_names: dict[int, str] = {}

        if point_ids:
            q = await db.execute(text("SELECT id, name FROM raid_points WHERE id = ANY(:ids)"), {"ids": list(point_ids)})
            for row in q.mappings().all():
                try:
                    point_names[int(row["id"])] = str(row.get("name") or f"Точка {row['id']}")
                except Exception:
                    continue

        if item_ids:
            q = await db.execute(text("SELECT id, name FROM items WHERE id = ANY(:ids)"), {"ids": list(item_ids)})
            for row in q.mappings().all():
                try:
                    item_names[int(row["id"])] = str(row.get("name") or f"item#{row['id']}")
                except Exception:
                    continue

        if char_ids:
            q = await db.execute(text("SELECT id, name FROM characters WHERE id = ANY(:ids)"), {"ids": list(char_ids)})
            for row in q.mappings().all():
                try:
                    char_names[int(row["id"])] = str(row.get("name") or f"персонаж#{row['id']}")
                except Exception:
                    continue

        return RaidLogContext(point_names=point_names, item_names=item_names, char_names=char_names)

    @staticmethod
    def _fmt_items(items: Any, item_names: dict[int, str]) -> str:
        if not isinstance(items, list) or not items:
            return "ничего"

        id_to_qty: dict[int, int] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                item_id = int(it.get("item_id"))
                qty = int(it.get("qty") or 1)
            except Exception:
                continue
            id_to_qty[item_id] = id_to_qty.get(item_id, 0) + max(1, qty)

        if not id_to_qty:
            return "ничего"

        parts: list[str] = []
        for item_id, qty in id_to_qty.items():
            nm = item_names.get(item_id) or f"item#{item_id}"
            parts.append(f"{esc_html(nm)} x{qty}")
        return ", ".join(parts)

    @staticmethod
    def _fmt_injuries_delta(delta: Any) -> str:
        if not isinstance(delta, dict):
            return ""

        mapping = {
            "head": "голова",
            "torso": "торс",
            "arm": "рука",
            "leg": "нога",
        }
        parts: list[str] = []
        for k, label in mapping.items():
            try:
                v = int(delta.get(k) or 0)
            except Exception:
                v = 0
            if v > 0:
                parts.append(f"{label} +{v}")
        return ", ".join(parts)

    @staticmethod
    def _fmt_missing_slots(slots: Any) -> str:
        if not isinstance(slots, list) or not slots:
            return ""
        xs: list[str] = []
        for s in slots:
            try:
                xs.append(str(int(s)))
            except Exception:
                continue
        return ", ".join(xs)

    @staticmethod
    def format_one(row: dict, ctx: RaidLogContext, *, now: datetime | None = None, for_notify: bool = False) -> str:
        now = now or utcnow()

        kind = str(row.get("kind") or "")
        payload = _parse_payload(row.get("payload"))

        point_name = ""
        try:
            if row.get("point_id") is not None:
                pid = int(row["point_id"])
                point_name = ctx.point_names.get(pid) or f"Точка {pid}"
        except Exception:
            point_name = ""

        if kind == "travel_started":
            eta = payload.get("travel_eta_at")
            eta_dt = None
            if isinstance(eta, str):
                try:
                    eta_dt = datetime.fromisoformat(eta)
                except Exception:
                    eta_dt = None
            rel = fmt_rel_eta(eta_dt, now=now)
            tail = f" ({rel})" if rel else ""
            return f"Переход к {esc_html(point_name)}{tail}"

        if kind == "arrived":
            return f"Прибыл на {esc_html(point_name)} – начал поиск"

        if kind == "heard_shots":
            return "Слышит стрельбу поблизости"

        if kind == "exit_started":
            eta = payload.get("exit_eta_at")
            eta_dt = None
            if isinstance(eta, str):
                try:
                    eta_dt = datetime.fromisoformat(eta)
                except Exception:
                    eta_dt = None
            rel = fmt_rel_eta(eta_dt, now=now)
            tail = f" ({rel})" if rel else ""
            return f"Уходит с рейда{tail}"

        if kind == "found_items":
            items = payload.get("items")
            return f"Нашел: {RaidEvents._fmt_items(items, ctx.item_names)}"

        if kind == "search_finished":
            looted = payload.get("looted")
            if isinstance(looted, list) and looted:
                return f"Поиск завершен – лут: {RaidEvents._fmt_items(looted, ctx.item_names)}"
            return "Поиск завершен – ничего"

        if kind == "fight_started":
            enemy_id = payload.get("enemy_character_id")
            enemy_name = "противник"
            try:
                if enemy_id is not None:
                    enemy_name = ctx.char_names.get(int(enemy_id)) or enemy_name
            except Exception:
                pass
            return f"Вступил в бой: {esc_html(enemy_name)}"

        if kind == "ammo_out":
            slots = RaidEvents._fmt_missing_slots(payload.get("missing_slots"))
            if slots:
                return f"Патроны закончились (слоты: {esc_html(slots)})"
            return "Патроны закончились"

        if kind == "corpse_searched":
            found = payload.get("found")
            weapon_id = payload.get("weapon_id")
            parts: list[str] = []
            if isinstance(found, list) and found:
                parts.append(f"вещи: {RaidEvents._fmt_items(found, ctx.item_names)}")
            if weapon_id is not None:
                try:
                    nm = ctx.item_names.get(int(weapon_id)) or f"weapon#{weapon_id}"
                    parts.append(f"оружие: {esc_html(nm)}")
                except Exception:
                    pass
            if parts:
                return "Обыскал противника – " + "; ".join(parts)
            return "Обыскал противника"

        if kind == "fight_finished":
            outcome = str(payload.get("outcome") or "")
            if outcome == "win":
                out = "победа"
            elif outcome == "lose":
                out = "поражение"
            else:
                out = "завершен"

            hp_after = payload.get("self_hp_after")
            hp_max = payload.get("self_hp_max")
            hp_line = ""
            try:
                if hp_after is not None and hp_max is not None:
                    hp_line = f" (HP {int(hp_after)}/{int(hp_max)})"
            except Exception:
                hp_line = ""

            inj = RaidEvents._fmt_injuries_delta(payload.get("self_injuries_delta"))
            inj_line = f" – ранение: {esc_html(inj)}" if inj else ""

            if for_notify:
                return f"Бой: {out}{hp_line}{inj_line}"

            return f"Бой завершен – {out}{hp_line}{inj_line}"

        if kind == "raid_finished":
            return "Рейд завершен"

        if kind == "raid_dead":
            return "Персонаж погиб в рейде"

        # Fallback
        if kind:
            return esc_html(kind)
        return ""
