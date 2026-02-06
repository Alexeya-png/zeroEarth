from __future__ import annotations

import copy
import random
from dataclasses import dataclass, replace
from typing import Iterable, Literal

from core.mechanics.shooting import calc_shooting_model, zone_probs, T_PART


Behavior = Literal["stealth", "aggressive"]


def clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def compute_defense_pct(head_armor_pct: float, body_armor_pct: float) -> float:
    h = clamp(float(head_armor_pct), 0.0, 100.0)
    b = clamp(float(body_armor_pct), 0.0, 100.0)
    return 100.0 * (1.0 - (1.0 - h / 100.0) * (1.0 - b / 100.0))


def geometric_mean_pct(values: Iterable[float]) -> float:
    xs = [float(v) for v in values if v is not None and float(v) > 0]
    if not xs:
        return 100.0
    prod = 1.0
    for v in xs:
        prod *= v
    return prod ** (1.0 / len(xs))


@dataclass(frozen=True)
class WeaponSnapshot:
    name: str
    category: str
    caliber: str
    accuracy: int
    reliability: int
    dmg: int
    ap: int


@dataclass
class InjuryState:
    head: int = 0
    torso: int = 0
    arm: int = 0
    leg: int = 0

    def add_from_dict(self, inj: dict[str, int]) -> None:
        self.head = min(3, self.head + int(inj.get("head", 0)))
        self.torso = min(3, self.torso + int(inj.get("torso", 0)))
        self.arm = min(3, self.arm + int(inj.get("arm", 0)))
        self.leg = min(3, self.leg + int(inj.get("leg", 0)))

    @property
    def total_severity(self) -> int:
        return int(self.head) + int(self.torso) + int(self.arm) + int(self.leg)


@dataclass
class CombatantState:
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
    behavior: Behavior
    injuries: InjuryState


@dataclass
class AttackEvent:
    attacker: str
    defender: str
    log_lines: list[str]


@dataclass(frozen=True)
class AmbushInfo:
    a_roll: int
    b_roll: int
    a_total: float
    b_total: float
    winner: str | None
    canceled: bool
    ambush_shot: bool
    ambush_shot_weapon: WeaponSnapshot | None
    auto_first_round: bool


@dataclass(frozen=True)
class RoundInfo:
    round_no: int
    a_weapon: WeaponSnapshot | None
    b_weapon: WeaponSnapshot | None
    a_d20: int
    b_d20: int
    a_total: float
    b_total: float
    first: str
    forced_first: bool
    a_skip: bool
    b_skip: bool


@dataclass
class ClashResult:
    a_start: CombatantState
    b_start: CombatantState

    planned_rounds: int
    ambush: AmbushInfo
    rounds_info: list[RoundInfo]
    events: list[AttackEvent]

    a_end: CombatantState
    b_end: CombatantState
    winner: str | None
    rounds_done: int


WEAPON_SPEED: dict[str, int] = {
    "pistol": 12,
    "smg": 8,
    "shotgun": 4,
    "rifle": 0,
    "sniper": -4,
    "lmg": -8,
}


def _weapon_speed(cat: str) -> int:
    return int(WEAPON_SPEED.get(str(cat), 0))


def _roll_ambush(c: CombatantState, rng: random.Random) -> tuple[int, float]:
    d100 = rng.randint(1, 100)
    total = float(d100) + float(c.stealth)
    return d100, total


def _pick_zone(weapon_category: str, rng: random.Random) -> str:
    probs = zone_probs(weapon_category)
    items = probs.items() if isinstance(probs, dict) else probs

    r = rng.random()
    acc = 0.0
    last = "torso"
    for k, p in items:
        last = str(k)
        acc += float(p)
        if r <= acc:
            return last
    return last


def _simulate_attack(
    *,
    weapon_category: str,
    shots: int,
    p_hit: float,
    p_jam: float,
    d_hit: float,
    rng: random.Random,
) -> dict:
    hits = 0
    jams = 0
    dmg_total = 0.0
    hits_by_zone: dict[str, int] = {"head": 0, "torso": 0, "arm": 0, "leg": 0}
    inj: dict[str, int] = {"head": 0, "torso": 0, "arm": 0, "leg": 0}

    for _ in range(max(0, int(shots))):
        if rng.random() < float(p_jam):
            jams += 1
            continue

        if rng.random() < float(p_hit):
            hits += 1
            dmg_total += float(d_hit)

            z = _pick_zone(weapon_category, rng)
            hits_by_zone[z] += 1

            t = float(T_PART.get(z, 20.0))
            add = int(float(d_hit) // t)
            if add > 0:
                inj[z] += add

    return {
        "hits": hits,
        "jams": jams,
        "total_damage": dmg_total,
        "hits_by_zone": {k: v for k, v in hits_by_zone.items() if v > 0},
        "inj": {k: v for k, v in inj.items() if v > 0},
    }


def _resolve_attack(attacker: CombatantState, defender: CombatantState, weapon: WeaponSnapshot, rng: random.Random) -> AttackEvent:
    model = calc_shooting_model(
        ACCc=int(attacker.accuracy),
        ACCw=int(weapon.accuracy),
        RELw=int(weapon.reliability),
        CAT=str(weapon.category),
        DMG=int(weapon.dmg),
        AP=int(weapon.ap),
        REAd=float(defender.reaction),
        DEFbase=float(defender.defense_base_pct),
        RELarmor=float(defender.rel_armor_pct),
    )

    shots = int(round(float(model["shots"])))
    sim = _simulate_attack(
        weapon_category=str(weapon.category),
        shots=shots,
        p_hit=float(model["p_hit"]),
        p_jam=float(model["p_jam"]),
        d_hit=float(model["d_hit"]),
        rng=rng,
    )

    dmg = int(round(float(sim["total_damage"])))
    defender.hp_current = max(0, int(defender.hp_current) - dmg)
    defender.injuries.add_from_dict(sim["inj"])

    lines: list[str] = []
    lines.append(f"{attacker.name} стреляет из {weapon.name} ({weapon.category}, {weapon.caliber}).")
    lines.append(
        f"p_hit={float(model['p_hit']):.2f}, p_jam={float(model['p_jam']):.2f}, shots={shots}, d_hit={float(model['d_hit']):.1f}."
    )
    lines.append(f"Итог: hits={sim['hits']}, jams={sim['jams']}, dmg={dmg}.")
    if sim["hits_by_zone"]:
        z = ", ".join([f"{k}:{v}" for k, v in sim["hits_by_zone"].items()])
        lines.append(f"Попадания по зонам: {z}.")
    if sim["inj"]:
        inj = ", ".join([f"{k}+{v}" for k, v in sim["inj"].items()])
        lines.append(f"Травмы: {inj}.")
    lines.append(f"HP {defender.name}: {defender.hp_current}/{defender.hp_max}.")

    return AttackEvent(attacker=attacker.name, defender=defender.name, log_lines=lines)


def _planned_rounds(a: CombatantState, b: CombatantState) -> int:
    if a.behavior == "aggressive" and b.behavior == "aggressive":
        return 3
    return 2


def _ambush_winner(a_total: float, b_total: float) -> str | None:
    diff = abs(float(a_total) - float(b_total))
    if diff < 50.0:
        return None
    return "a" if a_total > b_total else "b"


def _pick_ambush_weapon(c: CombatantState) -> WeaponSnapshot | None:
    pref = ("sniper", "lmg", "rifle")
    for cat in pref:
        for w in c.weapons:
            if str(w.category) == cat:
                return w
    return None


def _eligible_weapons(c: CombatantState, *, allow_sniper: bool) -> list[WeaponSnapshot]:
    ws = list(c.weapons or [])
    if not allow_sniper:
        ws = [w for w in ws if str(w.category) != "sniper"]
    return ws


def _choose_weapon_for_round(
    c: CombatantState,
    *,
    round_no: int,
    rng: random.Random,
    allow_sniper: bool,
    locked_round1: WeaponSnapshot | None,
    prev_category: str | None,
) -> tuple[WeaponSnapshot | None, bool]:
    if round_no == 1 and locked_round1 is not None:
        return locked_round1, False

    eligible = _eligible_weapons(c, allow_sniper=allow_sniper)
    if not eligible:
        return None, False

    if round_no >= 2 and prev_category is not None:
        alt = [w for w in eligible if str(w.category) != str(prev_category)]
        if alt:
            return rng.choice(alt), False

        return rng.choice(eligible), True

    return rng.choice(eligible), False


def _roll_initiative_round(c: CombatantState, weapon: WeaponSnapshot, rng: random.Random) -> tuple[int, float]:
    d20 = rng.randint(1, 20)
    total = float(d20) + float(c.initiative) + float(_weapon_speed(str(weapon.category)))
    return d20, total



def _apply_injury_effects(state: CombatantState) -> CombatantState:
    inj = state.injuries

    acc = int(state.accuracy) - (8 * int(inj.arm)) - (4 * int(inj.head))
    if acc < 0:
        acc = 0

    reaction = float(state.reaction)
    reaction *= max(0.0, 1.0 - 0.05 * float(inj.torso))
    reaction *= max(0.0, 1.0 - 0.03 * float(inj.leg))

    initiative = float(state.initiative)
    initiative *= max(0.0, 1.0 - 0.05 * float(inj.head))
    initiative *= max(0.0, 1.0 - 0.05 * float(inj.torso))
    initiative *= max(0.0, 1.0 - 0.08 * float(inj.leg))

    stealth = float(reaction) - (5.0 * float(inj.total_severity))

    return replace(
        state,
        accuracy=acc,
        reaction=reaction,
        initiative=initiative,
        stealth=stealth,
    )

def simulate_clash_round(
    a: CombatantState,
    b: CombatantState,
    *,
    max_rounds: int = 20,
    rng: random.Random | None = None,
) -> ClashResult:
    rng = rng or random.Random()

    if not a.weapons:
        raise ValueError("attacker has no weapons")
    if not b.weapons:
        raise ValueError("defender has no weapons")

    a_start = copy.deepcopy(a)
    b_start = copy.deepcopy(b)
    a = _apply_injury_effects(a)
    b = _apply_injury_effects(b)
    a_state = copy.deepcopy(a)
    b_state = copy.deepcopy(b)

    planned = min(_planned_rounds(a_state, b_state), int(max_rounds))

    events: list[AttackEvent] = []
    rounds_info: list[RoundInfo] = []

    a_d100, a_total = _roll_ambush(a_state, rng)
    b_d100, b_total = _roll_ambush(b_state, rng)
    win_key = _ambush_winner(a_total, b_total)

    ambush_winner_state: CombatantState | None = None
    ambush_loser_state: CombatantState | None = None
    winner_label: str | None = None
    if win_key == "a":
        ambush_winner_state = a_state
        ambush_loser_state = b_state
        winner_label = a_state.name
    elif win_key == "b":
        ambush_winner_state = b_state
        ambush_loser_state = a_state
        winner_label = b_state.name

    ambush_canceled = False
    ambush_shot = False
    ambush_shot_weapon: WeaponSnapshot | None = None
    auto_first_round = False

    allow_sniper_a = False
    allow_sniper_b = False
    locked_round1_a: WeaponSnapshot | None = None
    locked_round1_b: WeaponSnapshot | None = None
    forced_first_round_winner: str | None = None

    events.append(
        AttackEvent(
            attacker=a_state.name,
            defender=b_state.name,
            log_lines=[
                "Пре-бой – засада.",
                f"{a_state.name}: d100 {a_d100} + Stealth {float(a_state.stealth):.1f} = {float(a_total):.1f}",
                f"{b_state.name}: d100 {b_d100} + Stealth {float(b_state.stealth):.1f} = {float(b_total):.1f}",
            ],
        )
    )

    if ambush_winner_state is not None:
        if ambush_winner_state is a_state:
            allow_sniper_a = True
        else:
            allow_sniper_b = True

        events.append(
            AttackEvent(
                attacker=ambush_winner_state.name,
                defender=ambush_loser_state.name,
                log_lines=[f"Засаду получает: {ambush_winner_state.name}."],
            )
        )

        if ambush_winner_state.behavior == "stealth":
            ambush_canceled = True
            events.append(
                AttackEvent(
                    attacker=ambush_winner_state.name,
                    defender=ambush_loser_state.name,
                    log_lines=["Скрытный выиграл засаду – он не атакует. Бой не начинается."],
                )
            )
        else:
            ambush_wpn = _pick_ambush_weapon(ambush_winner_state)
            if ambush_wpn is not None:
                ambush_shot = True
                ambush_shot_weapon = ambush_wpn
                events.append(
                    AttackEvent(
                        attacker=ambush_winner_state.name,
                        defender=ambush_loser_state.name,
                        log_lines=[
                            f"Засада – внеочередной выстрел из {ambush_wpn.name} ({ambush_wpn.category}).",
                        ],
                    )
                )
                events.append(_resolve_attack(ambush_winner_state, ambush_loser_state, ambush_wpn, rng))

                if ambush_winner_state is a_state:
                    locked_round1_a = ambush_wpn
                else:
                    locked_round1_b = ambush_wpn

            else:
                auto_first_round = True
                forced_first_round_winner = ambush_winner_state.name
                events.append(
                    AttackEvent(
                        attacker=ambush_winner_state.name,
                        defender=ambush_loser_state.name,
                        log_lines=[
                            "Нет sniper, lmg и rifle – выстрела из засады нет.",
                            "Вместо этого – автопобеда инициативы в 1 раунде.",
                        ],
                    )
                )

    ambush = AmbushInfo(
        a_roll=a_d100,
        b_roll=b_d100,
        a_total=float(a_total),
        b_total=float(b_total),
        winner=winner_label,
        canceled=ambush_canceled,
        ambush_shot=ambush_shot,
        ambush_shot_weapon=ambush_shot_weapon,
        auto_first_round=auto_first_round,
    )

    if ambush_canceled:
        planned = 0

    if not ambush_canceled:
        if a_state.hp_current <= 0 or b_state.hp_current <= 0:
            planned = 0

    prev_cat_a: str | None = None
    prev_cat_b: str | None = None

    rounds_done = 0
    for r in range(1, planned + 1):
        rounds_done = r

        w_a, a_forced_same = _choose_weapon_for_round(
            a_state,
            round_no=r,
            rng=rng,
            allow_sniper=allow_sniper_a,
            locked_round1=locked_round1_a,
            prev_category=prev_cat_a,
        )
        w_b, b_forced_same = _choose_weapon_for_round(
            b_state,
            round_no=r,
            rng=rng,
            allow_sniper=allow_sniper_b,
            locked_round1=locked_round1_b,
            prev_category=prev_cat_b,
        )

        if w_a is None or w_b is None:
            break

        a_d20, a_total_i = _roll_initiative_round(a_state, w_a, rng)
        b_d20, b_total_i = _roll_initiative_round(b_state, w_b, rng)

        forced_first = False
        if r == 1 and forced_first_round_winner is not None:
            forced_first = True
            first_name = forced_first_round_winner
        else:
            if a_total_i > b_total_i:
                first_name = a_state.name
            elif b_total_i > a_total_i:
                first_name = b_state.name
            else:
                if a_d20 > b_d20:
                    first_name = a_state.name
                elif b_d20 > a_d20:
                    first_name = b_state.name
                else:
                    first_name = a_state.name if rng.random() < 0.5 else b_state.name

        a_skip = False
        b_skip = False
        if r >= 2 and a_forced_same:
            a_skip = rng.random() < 0.20
        if r >= 2 and b_forced_same:
            b_skip = rng.random() < 0.20

        rounds_info.append(
            RoundInfo(
                round_no=r,
                a_weapon=w_a,
                b_weapon=w_b,
                a_d20=a_d20,
                b_d20=b_d20,
                a_total=float(a_total_i),
                b_total=float(b_total_i),
                first=first_name,
                forced_first=forced_first,
                a_skip=a_skip,
                b_skip=b_skip,
            )
        )

        events.append(
            AttackEvent(
                attacker=a_state.name,
                defender=b_state.name,
                log_lines=[
                    f"Раунд {r}.",
                    f"{a_state.name} оружие: {w_a.name} ({w_a.category}).",
                    f"{b_state.name} оружие: {w_b.name} ({w_b.category}).",
                    f"Инициатива – {a_state.name}: d20 {a_d20} + {float(a_state.initiative):.1f} + {float(_weapon_speed(w_a.category)):.0f} = {float(a_total_i):.1f}",
                    f"Инициатива – {b_state.name}: d20 {b_d20} + {float(b_state.initiative):.1f} + {float(_weapon_speed(w_b.category)):.0f} = {float(b_total_i):.1f}",
                    f"Первым стреляет: {first_name}" + (" (авто)" if forced_first else ""),
                ],
            )
        )

        if first_name == a_state.name:
            first = a_state
            second = b_state
            w_first = w_a
            w_second = w_b
            first_skip = a_skip
            second_skip = b_skip
        else:
            first = b_state
            second = a_state
            w_first = w_b
            w_second = w_a
            first_skip = b_skip
            second_skip = a_skip

        if first_skip:
            events.append(
                AttackEvent(
                    attacker=first.name,
                    defender=second.name,
                    log_lines=["Не стреляет – закончились патроны (20%)."],
                )
            )
        else:
            events.append(_resolve_attack(first, second, w_first, rng))
            if second.hp_current <= 0:
                events.append(AttackEvent(attacker=first.name, defender=second.name, log_lines=[f"{second.name} погибает."]))
                break

        if second_skip:
            events.append(
                AttackEvent(
                    attacker=second.name,
                    defender=first.name,
                    log_lines=["Не стреляет – закончились патроны (20%)."],
                )
            )
        else:
            events.append(_resolve_attack(second, first, w_second, rng))
            if first.hp_current <= 0:
                events.append(AttackEvent(attacker=second.name, defender=first.name, log_lines=[f"{first.name} погибает."]))
                break

        prev_cat_a = str(w_a.category)
        prev_cat_b = str(w_b.category)

        if a_state.hp_current <= 0 or b_state.hp_current <= 0:
            break

    winner: str | None = None
    if ambush_canceled:
        winner = None
    elif a_state.hp_current <= 0 and b_state.hp_current <= 0:
        winner = None
    elif a_state.hp_current <= 0:
        winner = b_state.name
    elif b_state.hp_current <= 0:
        winner = a_state.name
    else:
        winner = None

    return ClashResult(
        a_start=a_start,
        b_start=b_start,
        planned_rounds=planned,
        ambush=ambush,
        rounds_info=rounds_info,
        events=events,
        a_end=a_state,
        b_end=b_state,
        winner=winner,
        rounds_done=rounds_done,
    )
