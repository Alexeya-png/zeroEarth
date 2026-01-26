from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple


def clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


CAT_PARAMS: Dict[str, Dict[str, float]] = {
    "pistol": {"shots": 1, "dmgM": 1.00, "apM": 1.00, "jamK": 1.00},
    "smg": {"shots": 2, "dmgM": 0.90, "apM": 0.90, "jamK": 1.20},
    "rifle": {"shots": 1, "dmgM": 1.10, "apM": 1.10, "jamK": 1.00},
    "shotgun": {"shots": 6, "dmgM": 1.20, "apM": 0.70, "jamK": 1.10},
    "sniper": {"shots": 1, "dmgM": 1.60, "apM": 1.40, "jamK": 0.90},
    "lmg": {"shots": 3, "dmgM": 0.95, "apM": 1.00, "jamK": 1.30},
}


CAT_RU: Dict[str, str] = {
    "pistol": "пистолет",
    "smg": "пистолет-пулемёт",
    "rifle": "винтовка",
    "shotgun": "дробовик",
    "sniper": "снайперка",
    "lmg": "пулемёт",
}


T_PART: Dict[str, int] = {"head": 18, "torso": 22, "arm": 14, "leg": 14}


def zone_probs(category: str) -> List[Tuple[str, float]]:
    head, torso, arm, leg = 0.10, 0.50, 0.20, 0.20
    if category == "sniper":
        head, torso = 0.15, 0.45
    elif category == "shotgun":
        head, torso = 0.05, 0.55

    total = head + torso + arm + leg
    if total <= 0:
        return [("head", 0.10), ("torso", 0.50), ("arm", 0.20), ("leg", 0.20)]

    head, torso, arm, leg = head / total, torso / total, arm / total, leg / total
    return [("head", head), ("torso", torso), ("arm", arm), ("leg", leg)]


def calc_shooting_model(
    *,
    ACCc: float,
    ACCw: float,
    RELw: float,
    CAT: str,
    DMG: float,
    AP: float,
    REAd: float,
    DEFbase: float,
    RELarmor: float,
) -> Dict[str, float]:
    p = CAT_PARAMS.get(CAT) or CAT_PARAMS["rifle"]
    shots = float(p["shots"])
    dmgM = float(p["dmgM"])
    apM = float(p["apM"])
    jamK = float(p["jamK"])

    AttackScore = ACCc + ACCw
    DodgeScore = 4 * REAd
    p_hit = clamp((AttackScore - DodgeScore + 20.0) / 100.0, 0.05, 0.95)

    p_jam = ((100.0 - RELw) / 100.0) ** 4 * jamK
    p_jam = clamp(p_jam, 0.0, 0.25)

    APt = AP * apM
    DEFe = DEFbase * (RELarmor / 100.0)
    denom = 10.0 + APt
    DEF = DEFe * 10.0 / denom if denom > 0 else DEFe

    Marmor = clamp(1.0 - (DEF / 100.0), 0.05, 1.0)
    Mrel = 0.70 + 0.30 * (RELw / 100.0)

    margin = AttackScore - DodgeScore
    Q = clamp(1.0 + margin / 120.0, 0.50, 1.25)

    d0 = (DMG * dmgM) / shots if shots > 0 else 0.0
    d_hit = d0 * Mrel * Marmor * Q
    exp_damage = shots * p_hit * (1.0 - p_jam) * d_hit

    return {
        "shots": shots,
        "dmgM": dmgM,
        "apM": apM,
        "jamK": jamK,
        "p_hit": p_hit,
        "p_jam": p_jam,
        "APt": APt,
        "DEF": DEF,
        "Marmor": Marmor,
        "Mrel": Mrel,
        "Q": Q,
        "d0": d0,
        "d_hit": d_hit,
        "exp_damage": exp_damage,
    }


def _zone_cdf(category: str) -> List[Tuple[str, float]]:
    probs = zone_probs(category)
    cum: List[Tuple[str, float]] = []
    acc = 0.0
    for part, pz in probs:
        acc += pz
        cum.append((part, acc))
    return cum


def pick_zone(category: str) -> str:
    cdf = _zone_cdf(category)
    r = random.random()
    for part, csum in cdf:
        if r <= csum:
            return part
    return "torso"


def simulate_series(
    *,
    attempts: int,
    shots: int,
    p_hit: float,
    p_jam: float,
    d_hit: float,
    category: str,
    mannequin_hp: int,
    mannequin_hp_max: int,
    t_part: Dict[str, int] | None = None,
) -> Dict[str, object]:
    t_part = t_part or T_PART

    hits_by_zone = {"head": 0, "torso": 0, "arm": 0, "leg": 0}
    inj = {"head": 0, "torso": 0, "arm": 0, "leg": 0}
    max_recovery_hours = 0.0

    total_damage = 0
    total_hits = 0
    total_jams = 0
    per_attack_damage: List[int] = []
    attempt_rows: List[str] = []

    cdf = _zone_cdf(category)

    def _pick_zone() -> str:
        r = random.random()
        for part, csum in cdf:
            if r <= csum:
                return part
        return "torso"

    for a in range(1, attempts + 1):
        hits_a = 0
        jams_a = 0
        sum_float = 0.0

        for _ in range(shots):
            jam = random.random() < p_jam
            hit = random.random() < p_hit

            if jam:
                jams_a += 1
                total_jams += 1

            if hit and (not jam):
                hits_a += 1
                total_hits += 1
                sum_float += d_hit

                part = _pick_zone()
                hits_by_zone[part] += 1

                inj_add = int(math.floor(d_hit / float(t_part[part])))
                if inj_add > 0:
                    inj[part] = min(3, inj[part] + inj_add)

                rec_add = 2.0 * d_hit + 6.0 * float(inj_add)
                max_recovery_hours = max(max_recovery_hours, rec_add)

        dmg_a = round_half_up(sum_float)
        per_attack_damage.append(dmg_a)

        mannequin_hp = max(0, mannequin_hp - dmg_a)
        total_damage += dmg_a

        attempt_rows.append(
            f"Атака {a}/{attempts} – попаданий {hits_a}/{shots} – осечек {jams_a}/{shots} – урон {dmg_a} – HP манекена {mannequin_hp}/{mannequin_hp_max}"
        )

    return {
        "mannequin_hp_end": mannequin_hp,
        "hits_by_zone": hits_by_zone,
        "inj": inj,
        "max_recovery_hours": float(max_recovery_hours),
        "total_damage": int(total_damage),
        "total_hits": int(total_hits),
        "total_jams": int(total_jams),
        "per_attack_damage": per_attack_damage,
        "attempt_rows": attempt_rows,
    }
