# tools/raid_loot_editor.py
# Tkinter editor for filling loot on raid points in raid locations
#
# Requirements
#   pip install "psycopg[binary]"
# or
#   pip install psycopg2-binary
#
# DATABASE_URL formats supported
# - postgresql://user:pass@host:5432/dbname?sslmode=require
# - postgresql+asyncpg://... (auto-normalized)
# - conninfo: host=... port=... dbname=... user=... password=... sslmode=require

from __future__ import annotations

import json
import os
import random
import re
import tkinter as tk
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from tkinter import messagebox, ttk
from urllib.parse import parse_qs, unquote

ITEM_TYPES_ORDER = [
    "head",
    "body",
    "gloves",
    "boots",
    "misc",
    "Weapon Upgrade",
    "Ammo",
    "weapon",
]


def _clamp_int(x: str, lo: int, hi: int, default: int) -> int:
    try:
        v = int(str(x).strip())
    except Exception:
        return default
    return max(lo, min(hi, v))


def _safe_str(x) -> str:
    return "" if x is None else str(x)


def _weighted_choice(keys: list[str], weights: list[int]) -> str:
    total = 0
    for w in weights:
        total += max(0, int(w))
    if total <= 0:
        return random.choice(keys)
    r = random.randint(1, total)
    acc = 0
    for k, w in zip(keys, weights):
        acc += max(0, int(w))
        if r <= acc:
            return k
    return keys[-1]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        if v is None or v == '':
            return default
        return int(v)
    except Exception:
        return default


def _search_eta(now: datetime) -> datetime:
    seconds = _env_int('RAID_SEARCH_SECONDS', 0)
    if seconds > 0:
        return now + timedelta(seconds=seconds)
    return now + timedelta(minutes=random.randint(10, 20))


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _normalize_dsn(dsn: str) -> str:
    dsn = (dsn or "").strip()
    dsn = re.sub(r"^postgresql\+asyncpg://", "postgresql://", dsn, flags=re.IGNORECASE)
    dsn = re.sub(r"^postgres\+asyncpg://", "postgresql://", dsn, flags=re.IGNORECASE)
    dsn = re.sub(r"^postgres://", "postgresql://", dsn, flags=re.IGNORECASE)
    dsn = dsn.replace("ssl=require", "sslmode=require")
    return dsn


def _conninfo_quote(v: str) -> str:
    v = "" if v is None else str(v)
    if v == "":
        return v
    if re.search(r"\s|['\\]", v):
        v = v.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{v}'"
    return v


def _dsn_to_conninfo(dsn: str) -> str:
    dsn = _normalize_dsn(dsn)
    if not dsn.lower().startswith("postgresql://"):
        return dsn

    base, sep, query = dsn.partition("?")
    q = parse_qs(query) if sep else {}
    sslmode = (q.get("sslmode") or q.get("ssl") or ["require"])[0]

    rest = base[len("postgresql://") :]

    user = ""
    password = ""
    host_and_path = rest

    if "@" in rest:
        userinfo, host_and_path = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
        else:
            user = userinfo
        user = unquote(user)
        password = unquote(password)

    hostport, _, dbname = host_and_path.partition("/")
    dbname = dbname or "postgres"

    host = hostport
    port = 5432

    if hostport.startswith("["):
        m = re.match(r"^\[(.+)\](?::(\d+))?$", hostport)
        if m:
            host = m.group(1)
            if m.group(2):
                port = int(m.group(2))
    else:
        if ":" in hostport:
            h, p = hostport.rsplit(":", 1)
            if p.isdigit():
                host = h
                port = int(p)

    if not host:
        raise RuntimeError("Invalid DATABASE_URL: host missing")

    parts = [
        f"host={_conninfo_quote(host)}",
        f"port={int(port)}",
        f"dbname={_conninfo_quote(dbname)}",
        f"sslmode={_conninfo_quote(sslmode)}",
    ]
    if user:
        parts.append(f"user={_conninfo_quote(user)}")
    if password:
        parts.append(f"password={_conninfo_quote(password)}")

    return " ".join(parts)


class PgClient:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._impl = None
        self._conn = None

    def connect(self) -> None:
        dsn = _dsn_to_conninfo(self.dsn)
        if not dsn:
            raise RuntimeError("Empty DATABASE_URL")

        try:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore

            self._impl = "psycopg3"
            self._conn = psycopg.connect(dsn, row_factory=dict_row)
            self._conn.autocommit = False
            return
        except Exception:
            pass

        try:
            import psycopg2  # type: ignore
            from psycopg2.extras import RealDictCursor  # type: ignore

            self._impl = "psycopg2"
            self._conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
            self._conn.autocommit = False
            return
        except Exception as e:
            raise RuntimeError("No PostgreSQL driver found. Install psycopg or psycopg2.") from e

    def close(self) -> None:
        try:
            if self._conn:
                self._conn.close()
        finally:
            self._conn = None

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            self._conn.rollback()

    def fetchall(self, sql: str, params: dict | None = None) -> list[dict]:
        if not self._conn:
            raise RuntimeError("Not connected")
        cur = self._conn.cursor()
        cur.execute(sql, params or {})
        rows = cur.fetchall()
        cur.close()
        return list(rows)

    def fetchone(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: dict | None = None) -> int:
        if not self._conn:
            raise RuntimeError("Not connected")
        cur = self._conn.cursor()
        cur.execute(sql, params or {})
        rc = cur.rowcount
        cur.close()
        return int(rc if rc is not None else 0)


@dataclass(frozen=True)
class LocationRow:
    id: int
    code: str
    name: str


@dataclass(frozen=True)
class PointRow:
    id: int
    code: str
    name: str


class RaidLootEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Raid Loot Editor")
        self.geometry("1260x760")
        self.minsize(1200, 720)

        self.db: PgClient | None = None
        self.locations: list[LocationRow] = []
        self.points: list[PointRow] = []

        self.selected_location_id: int | None = None
        self.selected_point_id: int | None = None
        self.selected_presence_raid_id: int | None = None

        self._build_ui()

        dsn = os.environ.get("DATABASE_URL", "").strip()
        if dsn:
            self.dsn_var.set(dsn)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="DATABASE_URL").grid(row=0, column=0, sticky="w")
        self.dsn_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.dsn_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Connect", command=self.on_connect).grid(row=0, column=2, sticky="e")

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(top, textvariable=self.status_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        left = ttk.Frame(main, padding=8)
        mid = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=2)
        main.add(mid, weight=3)
        main.add(right, weight=3)

        # Left
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=2)
        left.rowconfigure(7, weight=2)

        ttk.Label(left, text="Location").grid(row=0, column=0, sticky="w")
        self.location_var = tk.StringVar(value="")
        self.location_cb = ttk.Combobox(left, textvariable=self.location_var, state="disabled")
        self.location_cb.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        self.location_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_location_changed())

        ttk.Label(left, text="Points").grid(row=2, column=0, sticky="w")
        self.points_lb = tk.Listbox(left, exportselection=False, height=12)
        self.points_lb.grid(row=3, column=0, sticky="nsew", pady=(4, 8))
        self.points_lb.bind("<<ListboxSelect>>", lambda _e: self.on_point_selected())

        btns = ttk.Frame(left)
        btns.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)
        ttk.Button(btns, text="Refresh", command=self.refresh_locations).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Clear point loot", command=self.on_clear_point_loot).grid(row=0, column=1, sticky="ew")

        ttk.Separator(left, orient=tk.HORIZONTAL).grid(row=5, column=0, sticky="ew", pady=8)

        ttk.Label(left, text="Characters on location and points").grid(row=6, column=0, sticky="w")
        self.presence_tv = ttk.Treeview(
            left,
            columns=("raid_id", "char_id", "char_name", "point_name", "state", "combat"),
            show="headings",
            height=10,
        )
        self.presence_tv.heading("raid_id", text="Raid")
        self.presence_tv.heading("char_id", text="Char ID")
        self.presence_tv.heading("char_name", text="Name")
        self.presence_tv.heading("point_name", text="Point")
        self.presence_tv.heading("state", text="State")
        self.presence_tv.heading("combat", text="Combat")
        self.presence_tv.column("raid_id", width=70, anchor="e")
        self.presence_tv.column("char_id", width=70, anchor="e")
        self.presence_tv.column("char_name", width=140, anchor="w")
        self.presence_tv.column("point_name", width=180, anchor="w")
        self.presence_tv.column("state", width=90, anchor="w")
        self.presence_tv.column("combat", width=70, anchor="w")
        self.presence_tv.grid(row=7, column=0, sticky="nsew", pady=(4, 8))
        self.presence_tv.bind("<<TreeviewSelect>>", lambda _e: self.on_presence_selected())

        pbtns = ttk.Frame(left)
        pbtns.grid(row=8, column=0, sticky="ew")
        pbtns.columnconfigure(0, weight=1)
        ttk.Button(pbtns, text="Reload presence", command=self.reload_presence).grid(row=0, column=0, sticky="ew")

        ttk.Separator(left, orient=tk.HORIZONTAL).grid(row=9, column=0, sticky="ew", pady=8)

        ttk.Label(left, text="Bots for fight testing").grid(row=10, column=0, sticky="w")
        bots = ttk.Frame(left)
        bots.grid(row=11, column=0, sticky="ew")
        bots.columnconfigure(1, weight=1)

        ttk.Label(bots, text="Type").grid(row=0, column=0, sticky="w")
        self.bot_type_var = tk.StringVar(value="raider")
        self.bot_type_cb = ttk.Combobox(bots, textvariable=self.bot_type_var, values=["raider"], state="readonly", width=10)
        self.bot_type_cb.grid(row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(bots, text="Count").grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.bot_count_var = tk.StringVar(value="4")
        ttk.Entry(bots, textvariable=self.bot_count_var, width=6).grid(row=0, column=3, sticky="w", padx=(6, 0))

        bbtns = ttk.Frame(left)
        bbtns.grid(row=12, column=0, sticky="ew", pady=(6, 0))
        bbtns.columnconfigure(0, weight=1)
        bbtns.columnconfigure(1, weight=1)
        bbtns.columnconfigure(2, weight=1)
        ttk.Button(bbtns, text="Spawn on point", command=self.on_spawn_bots_point).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(bbtns, text="Remove bots on point", command=self.on_remove_bots_point).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(bbtns, text="Remove bots on location", command=self.on_remove_bots_location).grid(row=0, column=2, sticky="ew")

        # Mid
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(1, weight=2)
        mid.rowconfigure(4, weight=2)
        mid.rowconfigure(7, weight=2)

        ttk.Label(mid, text="Point loot").grid(row=0, column=0, sticky="w")
        self.loot_tv = ttk.Treeview(mid, columns=("item_id", "item_type", "name", "qty"), show="headings", height=10)
        self.loot_tv.heading("item_id", text="Item ID")
        self.loot_tv.heading("item_type", text="Type")
        self.loot_tv.heading("name", text="Name")
        self.loot_tv.heading("qty", text="Qty")
        self.loot_tv.column("item_id", width=90, anchor="w")
        self.loot_tv.column("item_type", width=120, anchor="w")
        self.loot_tv.column("name", width=420, anchor="w")
        self.loot_tv.column("qty", width=70, anchor="e")
        self.loot_tv.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        self.loot_tv.bind("<<TreeviewSelect>>", lambda _e: self.on_loot_row_selected())

        loot_btns = ttk.Frame(mid)
        loot_btns.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        loot_btns.columnconfigure(0, weight=1)
        loot_btns.columnconfigure(1, weight=1)
        loot_btns.columnconfigure(2, weight=1)
        ttk.Button(loot_btns, text="Remove selected", command=self.on_remove_selected_loot).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(loot_btns, text="Set qty", command=self.on_set_qty_selected_loot).grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        ttk.Button(loot_btns, text="Reload point", command=self.reload_point_views).grid(row=0, column=2, sticky="ew")

        ttk.Label(mid, text="Point item type weights").grid(row=3, column=0, sticky="w")
        self.weights_tv = ttk.Treeview(mid, columns=("item_type", "weight"), show="headings", height=8)
        self.weights_tv.heading("item_type", text="Type")
        self.weights_tv.heading("weight", text="Weight")
        self.weights_tv.column("item_type", width=220, anchor="w")
        self.weights_tv.column("weight", width=120, anchor="e")
        self.weights_tv.grid(row=4, column=0, sticky="nsew", pady=(4, 8))

        w_btns = ttk.Frame(mid)
        w_btns.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        w_btns.columnconfigure(0, weight=1)
        w_btns.columnconfigure(1, weight=1)
        w_btns.columnconfigure(2, weight=1)
        ttk.Button(w_btns, text="Add or update weight", command=self.on_add_update_weight).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(w_btns, text="Delete weight", command=self.on_delete_weight).grid(
            row=0, column=1, sticky="ew", padx=(0, 6)
        )
        ttk.Button(w_btns, text="Load defaults", command=self.on_load_default_weights).grid(row=0, column=2, sticky="ew")

        ttk.Label(mid, text="Selected raid inventory").grid(row=6, column=0, sticky="w")
        self.inv_tv = ttk.Treeview(
            mid,
            columns=("item_id", "item_type", "name", "qty", "source"),
            show="headings",
            height=9,
        )
        self.inv_tv.heading("item_id", text="Item ID")
        self.inv_tv.heading("item_type", text="Type")
        self.inv_tv.heading("name", text="Name")
        self.inv_tv.heading("qty", text="Qty")
        self.inv_tv.heading("source", text="Source point")
        self.inv_tv.column("item_id", width=90, anchor="w")
        self.inv_tv.column("item_type", width=120, anchor="w")
        self.inv_tv.column("name", width=360, anchor="w")
        self.inv_tv.column("qty", width=70, anchor="e")
        self.inv_tv.column("source", width=200, anchor="w")
        self.inv_tv.grid(row=7, column=0, sticky="nsew", pady=(4, 8))

        inv_btns = ttk.Frame(mid)
        inv_btns.grid(row=8, column=0, sticky="ew")
        inv_btns.columnconfigure(0, weight=1)
        ttk.Button(inv_btns, text="Reload inventory", command=self.reload_inventory).grid(row=0, column=0, sticky="ew")

        # Right
        right.columnconfigure(0, weight=1)
        right.rowconfigure(11, weight=1)

        ttk.Label(right, text="Manual add or update loot").grid(row=0, column=0, sticky="w")

        form = ttk.Frame(right)
        form.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Item type").grid(row=0, column=0, sticky="w")
        self.itemtype_var = tk.StringVar(value="")
        self.itemtype_cb = ttk.Combobox(form, textvariable=self.itemtype_var, state="disabled")
        self.itemtype_cb.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.itemtype_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_items_list())

        ttk.Label(form, text="Search").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.search_var = tk.StringVar(value="")
        ent = ttk.Entry(form, textvariable=self.search_var)
        ent.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ent.bind("<KeyRelease>", lambda _e: self.refresh_items_list())

        ttk.Label(form, text="Qty").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.qty_var = tk.StringVar(value="1")
        ttk.Entry(form, textvariable=self.qty_var, width=10).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        self.items_lb = tk.Listbox(right, exportselection=False, height=10)
        self.items_lb.grid(row=2, column=0, sticky="nsew", pady=(0, 8))

        act = ttk.Frame(right)
        act.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        act.columnconfigure(0, weight=1)
        act.columnconfigure(1, weight=1)
        ttk.Button(act, text="Add or update", command=self.on_add_update_loot).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(act, text="Use selected loot qty", command=self.on_copy_selected_loot_qty).grid(row=0, column=1, sticky="ew")

        sep = ttk.Separator(right, orient=tk.HORIZONTAL)
        sep.grid(row=4, column=0, sticky="ew", pady=6)

        ttk.Label(right, text="Random fill").grid(row=5, column=0, sticky="w")

        rnd = ttk.Frame(right)
        rnd.grid(row=6, column=0, sticky="ew", pady=(6, 8))
        rnd.columnconfigure(1, weight=1)
        rnd.columnconfigure(3, weight=1)

        ttk.Label(rnd, text="Items min").grid(row=0, column=0, sticky="w")
        self.rnd_min_items = tk.StringVar(value="0")
        ttk.Entry(rnd, textvariable=self.rnd_min_items, width=6).grid(row=0, column=1, sticky="w", padx=(8, 12))

        ttk.Label(rnd, text="Items max").grid(row=0, column=2, sticky="w")
        self.rnd_max_items = tk.StringVar(value="3")
        ttk.Entry(rnd, textvariable=self.rnd_max_items, width=6).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(rnd, text="Qty min").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.rnd_min_qty = tk.StringVar(value="1")
        ttk.Entry(rnd, textvariable=self.rnd_min_qty, width=6).grid(row=1, column=1, sticky="w", padx=(8, 12), pady=(6, 0))

        ttk.Label(rnd, text="Qty max").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.rnd_max_qty = tk.StringVar(value="1")
        ttk.Entry(rnd, textvariable=self.rnd_max_qty, width=6).grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(6, 0))

        self.rnd_clear_before = tk.BooleanVar(value=False)
        self.rnd_respect_weights = tk.BooleanVar(value=True)
        self.rnd_unique_items = tk.BooleanVar(value=True)

        ttk.Checkbutton(right, text="Clear existing loot before fill", variable=self.rnd_clear_before).grid(row=7, column=0, sticky="w")
        ttk.Checkbutton(right, text="Respect per point weights", variable=self.rnd_respect_weights).grid(row=8, column=0, sticky="w")
        ttk.Checkbutton(right, text="Unique items", variable=self.rnd_unique_items).grid(row=9, column=0, sticky="w")

        ttk.Label(right, text="Limit types for random fill").grid(row=10, column=0, sticky="w", pady=(10, 4))
        self.rnd_types_lb = tk.Listbox(right, exportselection=False, selectmode=tk.MULTIPLE, height=7)
        self.rnd_types_lb.grid(row=11, column=0, sticky="nsew", pady=(0, 8))

        rnd_btns = ttk.Frame(right)
        rnd_btns.grid(row=12, column=0, sticky="ew")
        rnd_btns.columnconfigure(0, weight=1)
        rnd_btns.columnconfigure(1, weight=1)
        ttk.Button(rnd_btns, text="Fill current point", command=self.on_random_fill_point).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(rnd_btns, text="Fill all points in location", command=self.on_random_fill_location).grid(row=0, column=1, sticky="ew")

    # ----- Connect and load -----

    def on_connect(self) -> None:
        dsn = self.dsn_var.get().strip()
        if not dsn:
            messagebox.showerror("Error", "DATABASE_URL is empty")
            return

        try:
            if self.db:
                self.db.close()
            self.db = PgClient(dsn)
            self.db.connect()
            self.status_var.set("Connected")
            self.location_cb.configure(state="readonly")
            self.itemtype_cb.configure(state="readonly")
            self.refresh_locations()
            self.refresh_item_types()
        except Exception as e:
            self.db = None
            self.status_var.set("Not connected")
            messagebox.showerror("Connect failed", str(e))

    def refresh_locations(self) -> None:
        try:
            db = self._require_connected()
            rows = db.fetchall(
                """
                SELECT id, code, name
                FROM raid_locations
                WHERE is_active = true
                ORDER BY id
                """
            )
            self.locations = [LocationRow(int(r["id"]), _safe_str(r["code"]), _safe_str(r["name"])) for r in rows]
            values = [f"{loc.id} | {loc.code} | {loc.name}" for loc in self.locations]
            self.location_cb["values"] = values
            if values:
                self.location_cb.current(0)
                self.on_location_changed()
            else:
                self.location_var.set("")
                self.selected_location_id = None
                self._set_points([])
                self._clear_presence_views()
                self._clear_inventory_view()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def refresh_item_types(self) -> None:
        try:
            db = self._require_connected()
            rows = db.fetchall("SELECT DISTINCT item_type FROM items ORDER BY item_type")
            types = [_safe_str(r["item_type"]) for r in rows if _safe_str(r.get("item_type"))]
            types = self._sort_item_types(types)
            self.itemtype_cb["values"] = types
            self.rnd_types_lb.delete(0, tk.END)
            for t in types:
                self.rnd_types_lb.insert(tk.END, t)
            if types:
                self.itemtype_cb.current(0)
                self.refresh_items_list()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ----- Selection -----

    def on_location_changed(self) -> None:
        idx = self.location_cb.current()
        if idx < 0 or idx >= len(self.locations):
            self.selected_location_id = None
            self._set_points([])
            self._clear_presence_views()
            self._clear_inventory_view()
            return
        self.selected_location_id = self.locations[idx].id
        self.load_points_for_location(self.selected_location_id)
        self.load_presence_for_location(self.selected_location_id)

    def load_points_for_location(self, location_id: int) -> None:
        try:
            db = self._require_connected()
            rows = db.fetchall(
                """
                SELECT id, code, name
                FROM raid_points
                WHERE location_id = %(lid)s
                ORDER BY id
                """,
                {"lid": int(location_id)},
            )
            self.points = [PointRow(int(r["id"]), _safe_str(r["code"]), _safe_str(r["name"])) for r in rows]
            self._set_points(self.points)
            if self.points:
                self.points_lb.selection_clear(0, tk.END)
                self.points_lb.selection_set(0)
                self.points_lb.activate(0)
                self.on_point_selected()
            else:
                self.selected_point_id = None
                self._clear_point_views()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _set_points(self, points: list[PointRow]) -> None:
        self.points_lb.delete(0, tk.END)
        for p in points:
            self.points_lb.insert(tk.END, f"{p.id} | {p.code} | {p.name}")

    def on_point_selected(self) -> None:
        sel = self.points_lb.curselection()
        if not sel:
            self.selected_point_id = None
            self._clear_point_views()
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.points):
            self.selected_point_id = None
            self._clear_point_views()
            return
        self.selected_point_id = self.points[idx].id
        self.reload_point_views()

    def reload_point_views(self) -> None:
        try:
            pid = self._require_point()
            self.load_loot_for_point(pid)
            self.load_weights_for_point(pid)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ----- Presence and inventory -----

    def reload_presence(self) -> None:
        try:
            lid = self._require_location()
            self.load_presence_for_location(lid)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_presence_for_location(self, location_id: int) -> None:
        db = self._require_connected()
        self._clear_presence_views()
        self.selected_presence_raid_id = None
        self._clear_inventory_view()

        rows = db.fetchall(
            """
            SELECT
                p.raid_id,
                p.character_id,
                c.name AS character_name,
                rp.name AS point_name,
                p.state,
                p.is_in_combat
            FROM raid_point_presence p
            JOIN raids r ON r.id = p.raid_id
            JOIN raid_points rp ON rp.id = p.point_id
            JOIN characters c ON c.id = p.character_id
            WHERE rp.location_id = %(lid)s
              AND r.status = 'active'
              AND p.state <> 'left'
            ORDER BY rp.id, c.name
            """,
            {"lid": int(location_id)},
        )

        for r in rows:
            raid_id = int(r["raid_id"])
            char_id = int(r["character_id"])
            char_name = _safe_str(r.get("character_name"))
            point_name = _safe_str(r.get("point_name"))
            state = _safe_str(r.get("state"))
            combat = "yes" if bool(r.get("is_in_combat")) else "no"
            self.presence_tv.insert("", tk.END, values=(raid_id, char_id, char_name, point_name, state, combat))

    def _clear_presence_views(self) -> None:
        for iid in self.presence_tv.get_children():
            self.presence_tv.delete(iid)

    def on_presence_selected(self) -> None:
        sel = self.presence_tv.selection()
        if not sel:
            self.selected_presence_raid_id = None
            self._clear_inventory_view()
            return
        row = self.presence_tv.item(sel[0], "values")
        raid_id = int(row[0])
        self.selected_presence_raid_id = raid_id
        self.load_raid_inventory(raid_id)

    def reload_inventory(self) -> None:
        try:
            if not self.selected_presence_raid_id:
                return
            self.load_raid_inventory(int(self.selected_presence_raid_id))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_raid_inventory(self, raid_id: int) -> None:
        db = self._require_connected()
        self._clear_inventory_view()

        rows = db.fetchall(
            """
            SELECT
                ri.item_id,
                i.item_type,
                i.name,
                ri.qty,
                rp.name AS source_point_name
            FROM raid_inventory ri
            JOIN items i ON i.id = ri.item_id
            LEFT JOIN raid_points rp ON rp.id = ri.source_point_id
            WHERE ri.raid_id = %(rid)s
            ORDER BY i.item_type, i.name
            """,
            {"rid": int(raid_id)},
        )

        for r in rows:
            self.inv_tv.insert(
                "",
                tk.END,
                values=(
                    int(r["item_id"]),
                    _safe_str(r.get("item_type")),
                    _safe_str(r.get("name")),
                    int(r.get("qty") or 0),
                    _safe_str(r.get("source_point_name")),
                ),
            )

    def _clear_inventory_view(self) -> None:
        for iid in self.inv_tv.get_children():
            self.inv_tv.delete(iid)

    # ----- Bots for fight testing -----

    def on_spawn_bots_point(self) -> None:
        try:
            db = self._require_connected()
            lid = self._require_location()
            pid = self._require_point()
            kind = (self.bot_type_var.get() or "raider").strip().lower()
            count = _clamp_int(self.bot_count_var.get(), 1, 30, 1)

            for _ in range(count):
                if kind == "raider":
                    self._spawn_raider_bot(int(lid), int(pid))
                else:
                    self._spawn_raider_bot(int(lid), int(pid))

            db.commit()
            self.reload_presence()
        except Exception as e:
            try:
                if self.db:
                    self.db.rollback()
            except Exception:
                pass
            messagebox.showerror("Error", str(e))

    def on_remove_bots_point(self) -> None:
        try:
            db = self._require_connected()
            lid = self._require_location()
            pid = self._require_point()
            now = _utcnow()

            db.execute(
                """
                UPDATE raid_point_presence rp
                SET state = 'left',
                    left_at = %(now)s
                FROM raids r
                JOIN raid_points p ON p.id = rp.point_id
                WHERE r.id = rp.raid_id
                  AND r.status = 'active'
                  AND p.location_id = %(lid)s
                  AND rp.point_id = %(pid)s
                  AND rp.is_in_combat = false
                  AND COALESCE((r.meta_json->>'is_bot')::boolean, false) = true
                """,
                {"now": now, "lid": int(lid), "pid": int(pid)},
            )

            db.execute(
                """
                UPDATE raids r
                SET status = 'finished',
                    ended_at = %(now)s
                WHERE r.status = 'active'
                  AND r.location_id = %(lid)s
                  AND r.current_point_id = %(pid)s
                  AND COALESCE((r.meta_json->>'is_bot')::boolean, false) = true
                """,
                {"now": now, "lid": int(lid), "pid": int(pid)},
            )

            db.commit()
            self.reload_presence()
        except Exception as e:
            try:
                if self.db:
                    self.db.rollback()
            except Exception:
                pass
            messagebox.showerror("Error", str(e))

    def on_remove_bots_location(self) -> None:
        try:
            db = self._require_connected()
            lid = self._require_location()
            now = _utcnow()

            db.execute(
                """
                UPDATE raid_point_presence rp
                SET state = 'left',
                    left_at = %(now)s
                FROM raids r
                JOIN raid_points p ON p.id = rp.point_id
                WHERE r.id = rp.raid_id
                  AND r.status = 'active'
                  AND p.location_id = %(lid)s
                  AND rp.is_in_combat = false
                  AND COALESCE((r.meta_json->>'is_bot')::boolean, false) = true
                """,
                {"now": now, "lid": int(lid)},
            )

            db.execute(
                """
                UPDATE raids r
                SET status = 'finished',
                    ended_at = %(now)s
                WHERE r.status = 'active'
                  AND r.location_id = %(lid)s
                  AND COALESCE((r.meta_json->>'is_bot')::boolean, false) = true
                """,
                {"now": now, "lid": int(lid)},
            )

            db.commit()
            self.reload_presence()
        except Exception as e:
            try:
                if self.db:
                    self.db.rollback()
            except Exception:
                pass
            messagebox.showerror("Error", str(e))

    def _spawn_raider_bot(self, location_id: int, point_id: int) -> None:
        db = self._require_connected()
        now = _utcnow()

        user_id = self._create_bot_user()
        character_id = self._create_raider_character(user_id)
        self._equip_raider_weapon(character_id)
        self._start_bot_raid(character_id, location_id, point_id, now)

    def _create_bot_user(self) -> int:
        db = self._require_connected()
        for _ in range(50):
            tg_id = -random.randint(1_000_000, 2_000_000_000)
            row = db.fetchone("SELECT id FROM users WHERE tg_id = %(tg)s", {"tg": int(tg_id)})
            if row:
                continue
            ins = db.fetchone("INSERT INTO users (tg_id) VALUES (%(tg)s) RETURNING id", {"tg": int(tg_id)})
            if ins and "id" in ins:
                return int(ins["id"])
        raise RuntimeError("Cannot allocate bot user")

    def _create_raider_character(self, user_id: int) -> int:
        db = self._require_connected()
        name = f"Рейдер {random.randint(1000, 9999)}"
        params = {
            "user_id": int(user_id),
            "name": name,
            "faction": "civilians",
            "creation_type": "free",
            "endurance": 2,
            "agility": 2,
            "intelligence": 1,
            "hp": 110,
            "carry_capacity": 80.0,
            "load": 0.0,
            "reaction": 8.0,
            "accuracy": 45,
            "initiative": 10.0,
            "stealth": 6.0,
            "tech_training": 0,
            "hacking": 0,
            "loot_analysis": 0,
            "loot_modding": 0,
            "repair": 0,
            "chem_modding": 0,
        }
        row = db.fetchone(
            """
            INSERT INTO characters (
              user_id, name, faction, creation_type, is_alive,
              endurance, agility, intelligence,
              hp, carry_capacity, load,
              reaction, accuracy, initiative, stealth,
              tech_training, hacking, loot_analysis, loot_modding, repair, chem_modding
            )
            VALUES (
              %(user_id)s, %(name)s, %(faction)s, %(creation_type)s, true,
              %(endurance)s, %(agility)s, %(intelligence)s,
              %(hp)s, %(carry_capacity)s, %(load)s,
              %(reaction)s, %(accuracy)s, %(initiative)s, %(stealth)s,
              %(tech_training)s, %(hacking)s, %(loot_analysis)s, %(loot_modding)s, %(repair)s, %(chem_modding)s
            )
            RETURNING id
            """,
            params,
        )
        if not row:
            raise RuntimeError("Cannot create bot character")
        cid = int(row["id"])
        db.execute("INSERT INTO equipment (character_id) VALUES (%(cid)s)", {"cid": cid})
        db.execute("INSERT INTO character_faction_profile (character_id) VALUES (%(cid)s)", {"cid": cid})
        return cid

    def _equip_raider_weapon(self, character_id: int) -> None:
        db = self._require_connected()

        w = db.fetchone(
            """
            SELECT id, caliber_id
            FROM weapons
            WHERE category IN ('pistol', 'smg', 'shotgun')
            ORDER BY random()
            LIMIT 1
            """
        )
        if not w:
            return

        weapon_id = int(w["id"])
        caliber_id = w.get("caliber_id")
        db.execute(
            "UPDATE equipment SET weapon_1_id = %(wid)s WHERE character_id = %(cid)s",
            {"wid": weapon_id, "cid": int(character_id)},
        )

        if caliber_id is None:
            return
        ammo = db.fetchone(
            """
            SELECT id
            FROM ammo_types
            WHERE caliber_id = %(cal)s
            ORDER BY id
            LIMIT 1
            """,
            {"cal": int(caliber_id)},
        )
        if not ammo:
            return

        db.execute(
            """
            INSERT INTO character_ammo_loadout(character_id, weapon_slot, ammo_type_id, qty)
            VALUES (%(cid)s, 1, %(ammo)s, %(qty)s)
            ON CONFLICT (character_id, weapon_slot)
            DO UPDATE SET ammo_type_id = EXCLUDED.ammo_type_id, qty = EXCLUDED.qty
            """,
            {"cid": int(character_id), "ammo": int(ammo["id"]), "qty": 30},
        )

    def _start_bot_raid(self, character_id: int, location_id: int, point_id: int, now: datetime) -> int:
        db = self._require_connected()
        meta = _json({"is_bot": True, "bot_kind": "raider"})

        row = db.fetchone(
            """
            INSERT INTO raids (
              character_id, location_id, status, phase,
              behavior_model, search_goal, current_point_id,
              started_at, search_limit_minutes, search_minutes_spent, meta_json
            )
            VALUES (
              %(cid)s, %(lid)s, 'active', 'searching',
              'aggressive', 'any', %(pid)s,
              %(now)s, 60, 0, CAST(%(meta)s AS jsonb)
            )
            RETURNING id
            """,
            {"cid": int(character_id), "lid": int(location_id), "pid": int(point_id), "now": now, "meta": meta},
        )
        if not row:
            raise RuntimeError("Cannot create bot raid")
        raid_id = int(row["id"])

        db.execute(
            """
            INSERT INTO raid_visited_points(raid_id, point_id, seq_no, visited_at)
            VALUES (%(rid)s, %(pid)s, 1, %(now)s)
            """,
            {"rid": raid_id, "pid": int(point_id), "now": now},
        )

        eta = _search_eta(now)
        db.execute(
            """
            INSERT INTO raid_point_presence(
              raid_id, character_id, point_id, state,
              arrived_at, search_started_at, search_eta_at,
              is_in_combat, meta_json
            )
            VALUES (
              %(rid)s, %(cid)s, %(pid)s, 'searching',
              %(now)s, %(now)s, %(eta)s,
              false, CAST(%(meta)s AS jsonb)
            )
            """,
            {"rid": raid_id, "cid": int(character_id), "pid": int(point_id), "now": now, "eta": eta, "meta": meta},
        )
        return raid_id

    # ----- Loot operations -----

    def _clear_point_views(self) -> None:
        for tv in (self.loot_tv, self.weights_tv):
            for iid in tv.get_children():
                tv.delete(iid)

    def load_loot_for_point(self, point_id: int) -> None:
        db = self._require_connected()
        for iid in self.loot_tv.get_children():
            self.loot_tv.delete(iid)

        rows = db.fetchall(
            """
            SELECT l.item_id, i.item_type, i.name, l.qty
            FROM raid_point_loot l
            JOIN items i ON i.id = l.item_id
            WHERE l.point_id = %(pid)s
            ORDER BY i.item_type, i.name
            """,
            {"pid": int(point_id)},
        )

        for r in rows:
            self.loot_tv.insert(
                "",
                tk.END,
                values=(int(r["item_id"]), _safe_str(r["item_type"]), _safe_str(r["name"]), int(r["qty"])),
            )

    def on_remove_selected_loot(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            sel = self.loot_tv.selection()
            if not sel:
                return
            row = self.loot_tv.item(sel[0], "values")
            item_id = int(row[0])
            db.execute(
                "DELETE FROM raid_point_loot WHERE point_id = %(pid)s AND item_id = %(iid)s",
                {"pid": pid, "iid": item_id},
            )
            db.commit()
            self.load_loot_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_set_qty_selected_loot(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            sel = self.loot_tv.selection()
            if not sel:
                return
            row = self.loot_tv.item(sel[0], "values")
            item_id = int(row[0])

            qty = _clamp_int(self.qty_var.get(), 1, 10_000, 1)
            db.execute(
                """
                INSERT INTO raid_point_loot(point_id, item_id, qty, spawned_at, updated_at)
                VALUES (%(pid)s, %(iid)s, %(qty)s, now(), now())
                ON CONFLICT (point_id, item_id)
                DO UPDATE SET qty = EXCLUDED.qty, updated_at = now()
                """,
                {"pid": pid, "iid": item_id, "qty": qty},
            )
            db.commit()
            self.load_loot_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_clear_point_loot(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            if not messagebox.askyesno("Confirm", "Clear all loot on selected point"):
                return
            db.execute("DELETE FROM raid_point_loot WHERE point_id = %(pid)s", {"pid": pid})
            db.commit()
            self.load_loot_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_copy_selected_loot_qty(self) -> None:
        sel = self.loot_tv.selection()
        if not sel:
            return
        row = self.loot_tv.item(sel[0], "values")
        self.qty_var.set(str(row[3]))

    def on_loot_row_selected(self) -> None:
        sel = self.loot_tv.selection()
        if not sel:
            return
        row = self.loot_tv.item(sel[0], "values")
        self.qty_var.set(str(row[3]))

    def on_add_update_loot(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            iid = self._selected_item_id_from_items_lb()
            if not iid:
                return

            qty = _clamp_int(self.qty_var.get(), 1, 10_000, 1)
            db.execute(
                """
                INSERT INTO raid_point_loot(point_id, item_id, qty, spawned_at, updated_at)
                VALUES (%(pid)s, %(iid)s, %(qty)s, now(), now())
                ON CONFLICT (point_id, item_id)
                DO UPDATE SET qty = EXCLUDED.qty, updated_at = now()
                """,
                {"pid": pid, "iid": int(iid), "qty": qty},
            )
            db.commit()
            self.load_loot_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    # ----- Weights operations -----

    def load_weights_for_point(self, point_id: int) -> None:
        db = self._require_connected()
        for iid in self.weights_tv.get_children():
            self.weights_tv.delete(iid)

        rows = db.fetchall(
            """
            SELECT item_type, weight
            FROM raid_point_itemtype_weights
            WHERE point_id = %(pid)s
            ORDER BY item_type
            """,
            {"pid": int(point_id)},
        )

        for r in rows:
            self.weights_tv.insert("", tk.END, values=(_safe_str(r["item_type"]), int(r["weight"])))

    def on_add_update_weight(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()

            t = (self.itemtype_var.get() or "").strip()
            if not t:
                return

            w = _clamp_int(self.qty_var.get(), 0, 10_000, 1)
            db.execute(
                """
                INSERT INTO raid_point_itemtype_weights(point_id, item_type, weight)
                VALUES (%(pid)s, %(t)s, %(w)s)
                ON CONFLICT (point_id, item_type)
                DO UPDATE SET weight = EXCLUDED.weight
                """,
                {"pid": pid, "t": t, "w": w},
            )
            db.commit()
            self.load_weights_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_delete_weight(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            sel = self.weights_tv.selection()
            if not sel:
                return
            row = self.weights_tv.item(sel[0], "values")
            t = _safe_str(row[0])
            db.execute(
                "DELETE FROM raid_point_itemtype_weights WHERE point_id = %(pid)s AND item_type = %(t)s",
                {"pid": pid, "t": t},
            )
            db.commit()
            self.load_weights_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_load_default_weights(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            item_types = self._get_item_types()
            if not item_types:
                return
            for t in item_types:
                db.execute(
                    """
                    INSERT INTO raid_point_itemtype_weights(point_id, item_type, weight)
                    VALUES (%(pid)s, %(t)s, 1)
                    ON CONFLICT (point_id, item_type)
                    DO NOTHING
                    """,
                    {"pid": pid, "t": t},
                )
            db.commit()
            self.load_weights_for_point(pid)
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    # ----- Random fill -----

    def _get_selected_random_types(self) -> list[str]:
        idxs = self.rnd_types_lb.curselection()
        if not idxs:
            return []
        out = []
        for i in idxs:
            out.append(_safe_str(self.rnd_types_lb.get(i)))
        return [t for t in out if t.strip()]

    def _get_point_weights_map(self, point_id: int) -> dict[str, int]:
        db = self._require_connected()
        rows = db.fetchall(
            "SELECT item_type, weight FROM raid_point_itemtype_weights WHERE point_id = %(pid)s",
            {"pid": int(point_id)},
        )
        m: dict[str, int] = {}
        for r in rows:
            t = _safe_str(r.get("item_type"))
            w = int(r.get("weight") or 0)
            if t:
                m[t] = w
        return m

    def _random_fill_point(self, point_id: int) -> None:
        db = self._require_connected()

        min_items = _clamp_int(self.rnd_min_items.get(), 0, 50, 0)
        max_items = _clamp_int(self.rnd_max_items.get(), 0, 50, 3)
        if max_items < min_items:
            max_items = min_items

        min_qty = _clamp_int(self.rnd_min_qty.get(), 1, 10_000, 1)
        max_qty = _clamp_int(self.rnd_max_qty.get(), 1, 10_000, 1)
        if max_qty < min_qty:
            max_qty = min_qty

        count = random.randint(min_items, max_items)

        if self.rnd_clear_before.get():
            db.execute("DELETE FROM raid_point_loot WHERE point_id = %(pid)s", {"pid": int(point_id)})

        limit_types = self._get_selected_random_types()
        weights_map = self._get_point_weights_map(point_id) if self.rnd_respect_weights.get() else {}

        if limit_types:
            eligible_types = limit_types
        else:
            eligible_types = list(weights_map.keys()) if weights_map else self._get_item_types()

        eligible_types = [t for t in eligible_types if t.strip()]
        if not eligible_types:
            return

        type_weights = [max(0, int(weights_map.get(t, 1))) for t in eligible_types]

        existing = set()
        if self.rnd_unique_items.get():
            rows = db.fetchall("SELECT item_id FROM raid_point_loot WHERE point_id = %(pid)s", {"pid": int(point_id)})
            for r in rows:
                existing.add(int(r["item_id"]))

        attempts_limit = 400

        for _ in range(count):
            t = _weighted_choice(eligible_types, type_weights)

            pick = db.fetchone("SELECT id FROM items WHERE item_type = %(t)s ORDER BY random() LIMIT 1", {"t": t})
            if not pick:
                continue

            item_id = int(pick["id"])
            if self.rnd_unique_items.get():
                tries = 0
                while item_id in existing and tries < 15:
                    pick = db.fetchone(
                        "SELECT id FROM items WHERE item_type = %(t)s ORDER BY random() LIMIT 1",
                        {"t": t},
                    )
                    if not pick:
                        break
                    item_id = int(pick["id"])
                    tries += 1
                if item_id in existing:
                    attempts_limit -= 1
                    if attempts_limit <= 0:
                        break
                    continue
                existing.add(item_id)

            qty = random.randint(min_qty, max_qty)
            db.execute(
                """
                INSERT INTO raid_point_loot(point_id, item_id, qty, spawned_at, updated_at)
                VALUES (%(pid)s, %(iid)s, %(qty)s, now(), now())
                ON CONFLICT (point_id, item_id)
                DO UPDATE SET qty = EXCLUDED.qty, updated_at = now()
                """,
                {"pid": int(point_id), "iid": int(item_id), "qty": int(qty)},
            )

    def on_random_fill_point(self) -> None:
        try:
            db = self._require_connected()
            pid = self._require_point()
            self._random_fill_point(pid)
            db.commit()
            self.reload_point_views()
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    def on_random_fill_location(self) -> None:
        try:
            db = self._require_connected()
            lid = self._require_location()
            rows = db.fetchall("SELECT id FROM raid_points WHERE location_id = %(lid)s ORDER BY id", {"lid": int(lid)})
            pids = [int(r["id"]) for r in rows]
            if not pids:
                return
            if not messagebox.askyesno("Confirm", "Fill all points in selected location"):
                return
            for pid in pids:
                self._random_fill_point(pid)
            db.commit()
            self.reload_point_views()
        except Exception as e:
            if self.db:
                self.db.rollback()
            messagebox.showerror("Error", str(e))

    # ----- Items list -----

    def _sort_item_types(self, types: list[str]) -> list[str]:
        order = {t: i for i, t in enumerate(ITEM_TYPES_ORDER)}
        return sorted(types, key=lambda x: (order.get(x, 999), x))

    def _get_item_types(self) -> list[str]:
        types = list(self.itemtype_cb["values"] or [])
        return [str(t) for t in types if str(t).strip()]

    def refresh_items_list(self) -> None:
        self.items_lb.delete(0, tk.END)
        if not self.db:
            return
        t = (self.itemtype_var.get() or "").strip()
        if not t:
            return

        q = (self.search_var.get() or "").strip()
        rows = self._load_items_for_type(t, q)
        for r in rows:
            self.items_lb.insert(
                tk.END,
                f"{int(r['id'])} | {r['name']} | price {int(r.get('price') or 0)} | w {r.get('weight')}",
            )

    def _load_items_for_type(self, item_type: str, search: str) -> list[dict]:
        db = self._require_connected()

        search = (search or "").strip()
        if len(search) > 80:
            search = search[:80]

        params = {"t": item_type}
        sql = """
            SELECT id, name, item_type, price, weight
            FROM items
            WHERE item_type = %(t)s
        """

        if search:
            s = re.sub(r"\s+", " ", search)
            params["q"] = f"%{s}%"
            sql += " AND name ILIKE %(q)s"

        sql += " ORDER BY name LIMIT 250"
        return db.fetchall(sql, params)

    def _selected_item_id_from_items_lb(self) -> int | None:
        sel = self.items_lb.curselection()
        if not sel:
            return None
        s = self.items_lb.get(sel[0])
        m = re.match(r"^\s*(\d+)\s*\|", s)
        if not m:
            return None
        return int(m.group(1))

    # ----- Require helpers -----

    def _require_connected(self) -> PgClient:
        if not self.db:
            raise RuntimeError("Not connected")
        return self.db

    def _require_point(self) -> int:
        if not self.selected_point_id:
            raise RuntimeError("Point not selected")
        return int(self.selected_point_id)

    def _require_location(self) -> int:
        if not self.selected_location_id:
            raise RuntimeError("Location not selected")
        return int(self.selected_location_id)


if __name__ == "__main__":
    app = RaidLootEditorApp()
    app.mainloop()
