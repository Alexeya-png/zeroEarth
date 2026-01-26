import asyncio
import os
import json
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

load_dotenv()

AMMO_TABLE = "ammo_types"  # если у тебя таблица называется иначе, поменяй тут


def normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q.pop("statement_cache_size", None)
    if "ssl" not in q:
        q["ssl"] = "require"
    new_query = urlencode(q)
    return urlunparse(p._replace(query=new_query))


@dataclass(frozen=True)
class WeaponSeed:
    name: str
    category: str
    caliber_code: str
    accuracy: int
    reliability: int


@dataclass(frozen=True)
class AmmoSeed:
    caliber_code: str
    bullet_type: str
    damage: int
    armor_penetration: int


CALIBERS: Dict[str, str] = {
    "9x18": "9×18",
    "762x25": "7.62×25",
    "25acp": ".25 ACP",
    "32acp": ".32 ACP",
    "9x19": "9×19",
    "40sw": ".40 S&W",
    "45acp": ".45 ACP",
    "57x28": "5.7×28",
    "46x30": "4.6×30",
    "545x39": "5.45×39",
    "762x39": "7.62×39",
    "556x45": "5.56×45",
    "762x51": "7.62×51",
    "762x54r": "7.62×54R",
    "12ga": "12ga",
    "338lm": ".338 Lapua",
    "50bmg": ".50 BMG",
}

# Показатели accuracy и reliability тут балансные для игры, модели оружия реальные
WEAPONS: List[WeaponSeed] = [
    # D
    WeaponSeed("SA80 L85A1", "rifle", "556x45", 60, 45),
    WeaponSeed("TEC-9", "smg", "9x19", 58, 50),
    WeaponSeed("Hi-Point C9", "pistol", "9x19", 55, 55),
    WeaponSeed("Kel-Tec PF-9", "pistol", "9x19", 56, 52),
    WeaponSeed("Ingram M11/9", "smg", "9x19", 54, 58),
    WeaponSeed("MAC-10", "smg", "45acp", 55, 56),
    WeaponSeed("Makarov PM", "pistol", "9x18", 55, 58),
    WeaponSeed("Tokarev TT-33", "pistol", "762x25", 56, 56),
    WeaponSeed("Skorpion vz.61", "smg", "32acp", 55, 55),
    WeaponSeed("Walther PPK", "pistol", "32acp", 58, 50),
    WeaponSeed("Beretta 950 Jetfire", "pistol", "25acp", 52, 60),
    WeaponSeed("Mossberg Maverick 88", "shotgun", "12ga", 55, 55),
    WeaponSeed("Saiga-12", "shotgun", "12ga", 50, 68),
    WeaponSeed("CETME Model L", "rifle", "556x45", 57, 52),
    WeaponSeed("RPK", "lmg", "762x39", 56, 54),
    WeaponSeed("RPD", "lmg", "762x39", 63, 66),
    WeaponSeed("Ruger Mini-14", "rifle", "556x45", 58, 52),
    WeaponSeed("SKS", "rifle", "762x39", 64, 66),
    WeaponSeed("PP-91 Kedr", "smg", "9x18", 52, 60),
    WeaponSeed("Taurus PT111 G2", "pistol", "9x19", 57, 52),

    # C
    WeaponSeed("SIG Sauer P250", "pistol", "9x19", 64, 62),
    WeaponSeed("Glock 26", "pistol", "9x19", 65, 62),
    WeaponSeed("Browning Hi-Power", "pistol", "9x19", 66, 62),
    WeaponSeed("CZ 75B", "pistol", "9x19", 67, 60),
    WeaponSeed("Springfield XD", "pistol", "9x19", 66, 60),
    WeaponSeed("Beretta PX4 Storm", "pistol", "9x19", 64, 66),
    WeaponSeed("Ruger SR9", "pistol", "9x19", 66, 61),
    WeaponSeed("PP-19 Bizon", "smg", "9x19", 64, 64),
    WeaponSeed("MP5K", "smg", "9x19", 66, 62),
    WeaponSeed("UMP45", "smg", "45acp", 63, 65),
    WeaponSeed("CZ Scorpion Evo 3", "smg", "9x19", 67, 60),
    WeaponSeed("AK-47", "rifle", "762x39", 65, 64),
    WeaponSeed("M16A2", "rifle", "556x45", 68, 60),
    WeaponSeed("FN FAL", "rifle", "762x51", 67, 61),
    WeaponSeed("Ithaca 37", "shotgun", "12ga", 62, 66),
    WeaponSeed("Stevens 320", "shotgun", "12ga", 60, 65),
    WeaponSeed("DP-28", "lmg", "762x54r", 68, 56),
    WeaponSeed("M60", "lmg", "762x51", 64, 64),
    WeaponSeed("Ruger PC Carbine", "smg", "9x19", 66, 62),
    WeaponSeed("Remington R51", "pistol", "9x19", 62, 64),

    # B
    WeaponSeed("Colt M1911", "pistol", "45acp", 70, 85),
    WeaponSeed("Beretta 92A1", "pistol", "9x19", 72, 83),
    WeaponSeed("SIG Sauer P226", "pistol", "9x19", 74, 80),
    WeaponSeed("HK USP", "pistol", "9x19", 73, 82),
    WeaponSeed("Glock 22", "pistol", "40sw", 72, 82),
    WeaponSeed("Uzi", "smg", "9x19", 68, 82),
    WeaponSeed("MPX", "smg", "9x19", 74, 80),
    WeaponSeed("B&T APC9", "smg", "9x19", 74, 81),
    WeaponSeed("PP-19-01 Vityaz", "smg", "9x19", 72, 80),
    WeaponSeed("FN P90", "smg", "57x28", 82, 86),
    WeaponSeed("AR-15", "rifle", "556x45", 78, 77),
    WeaponSeed("G36", "rifle", "556x45", 76, 79),
    WeaponSeed("FAMAS F1", "rifle", "556x45", 76, 80),
    WeaponSeed("AUG A3", "rifle", "556x45", 77, 78),
    WeaponSeed("Mossberg 500", "shotgun", "12ga", 64, 92),
    WeaponSeed("Remington 870", "shotgun", "12ga", 66, 90),
    WeaponSeed("Franchi SPAS-12", "shotgun", "12ga", 68, 86),
    WeaponSeed("M249", "lmg", "556x45", 72, 84),
    WeaponSeed("PKM", "lmg", "762x54r", 70, 89),
    WeaponSeed("FN Five-seveN", "pistol", "57x28", 75, 78),

    # A
    WeaponSeed("Glock 17", "pistol", "9x19", 78, 92),
    WeaponSeed("Beretta 92FS", "pistol", "9x19", 76, 90),
    WeaponSeed("SIG Sauer P320", "pistol", "9x19", 78, 88),
    WeaponSeed("HK VP9", "pistol", "9x19", 79, 87),
    WeaponSeed("MP5", "smg", "9x19", 80, 88),
    WeaponSeed("MP7", "smg", "46x30", 82, 86),
    WeaponSeed("KRISS Vector", "smg", "45acp", 79, 88),
    WeaponSeed("AKM", "rifle", "762x39", 72, 92),
    WeaponSeed("AK-74", "rifle", "545x39", 74, 93),
    WeaponSeed("AK-103", "rifle", "762x39", 76, 90),
    WeaponSeed("M4A1", "rifle", "556x45", 82, 86),
    WeaponSeed("HK416", "rifle", "556x45", 84, 86),
    WeaponSeed("IWI Tavor X95", "rifle", "556x45", 82, 86),
    WeaponSeed("FN SCAR-H", "rifle", "762x51", 84, 84),
    WeaponSeed("Galil ACE", "rifle", "762x39", 80, 88),
    WeaponSeed("Mossberg 590A1", "shotgun", "12ga", 72, 92),
    WeaponSeed("Benelli M4 Super 90", "shotgun", "12ga", 88, 94),
    WeaponSeed("PKP Pecheneg", "lmg", "762x54r", 74, 92),
    WeaponSeed("M240B", "lmg", "762x51", 76, 90),
    WeaponSeed("SVD Dragunov", "sniper", "762x54r", 88, 85),
    WeaponSeed("M24", "sniper", "762x51", 90, 82),
    WeaponSeed("Remington 700", "sniper", "762x51", 86, 82),
    WeaponSeed("FN SCAR 17S", "rifle", "762x51", 92, 91),
    WeaponSeed("CZ Shadow 2", "pistol", "9x19", 84, 82),
    WeaponSeed("SIG MCX", "rifle", "556x45", 84, 85),

    # S
    WeaponSeed("HK416A5", "rifle", "556x45", 92, 92),
    WeaponSeed("LMT MARS-L", "rifle", "556x45", 91, 93),
    WeaponSeed("Glock 34", "pistol", "9x19", 90, 92),
    WeaponSeed("HK MK23", "pistol", "45acp", 88, 95),
    WeaponSeed("HK MP5SD", "smg", "9x19", 88, 94),
    WeaponSeed("B&T APC9 Pro", "smg", "9x19", 90, 92),
    WeaponSeed("FN P90 TR", "smg", "57x28", 91, 91),
    WeaponSeed("HK MG5", "lmg", "762x51", 90, 92),
    WeaponSeed("FN Minimi Para", "lmg", "556x45", 88, 93),
    WeaponSeed("Sako TRG-42", "sniper", "338lm", 94, 92),
    WeaponSeed("Accuracy International AXMC", "sniper", "338lm", 95, 92),
    WeaponSeed("Barrett M107", "sniper", "50bmg", 90, 95),
]

AMMO: List[AmmoSeed] = [
    AmmoSeed("9x18", "FMJ", 16, 2),
    AmmoSeed("9x18", "JHP", 18, 1),
    AmmoSeed("9x18", "AP", 14, 4),

    AmmoSeed("762x25", "FMJ", 18, 3),
    AmmoSeed("762x25", "JHP", 20, 2),
    AmmoSeed("762x25", "AP", 16, 5),

    AmmoSeed("25acp", "FMJ", 10, 1),
    AmmoSeed("25acp", "JHP", 12, 0),
    AmmoSeed("25acp", "AP", 9, 2),

    AmmoSeed("32acp", "FMJ", 12, 1),
    AmmoSeed("32acp", "JHP", 14, 0),
    AmmoSeed("32acp", "AP", 11, 2),

    AmmoSeed("9x19", "FMJ", 18, 2),
    AmmoSeed("9x19", "JHP", 22, 1),
    AmmoSeed("9x19", "AP", 16, 5),

    AmmoSeed("40sw", "FMJ", 22, 3),
    AmmoSeed("40sw", "JHP", 26, 2),
    AmmoSeed("40sw", "AP", 20, 6),

    AmmoSeed("45acp", "FMJ", 24, 2),
    AmmoSeed("45acp", "JHP", 28, 1),
    AmmoSeed("45acp", "AP", 22, 5),

    AmmoSeed("57x28", "FMJ", 17, 4),
    AmmoSeed("57x28", "AP", 15, 7),
    AmmoSeed("57x28", "HP", 20, 2),

    AmmoSeed("46x30", "FMJ", 16, 4),
    AmmoSeed("46x30", "AP", 14, 7),
    AmmoSeed("46x30", "HP", 19, 2),

    AmmoSeed("545x39", "FMJ", 28, 6),
    AmmoSeed("545x39", "SP", 32, 4),
    AmmoSeed("545x39", "AP", 26, 10),

    AmmoSeed("762x39", "FMJ", 32, 7),
    AmmoSeed("762x39", "SP", 36, 5),
    AmmoSeed("762x39", "AP", 30, 11),

    AmmoSeed("556x45", "FMJ", 27, 7),
    AmmoSeed("556x45", "SP", 31, 5),
    AmmoSeed("556x45", "AP", 25, 11),

    AmmoSeed("762x51", "FMJ", 38, 10),
    AmmoSeed("762x51", "Match", 40, 9),
    AmmoSeed("762x51", "AP", 36, 14),

    AmmoSeed("762x54r", "FMJ", 40, 11),
    AmmoSeed("762x54r", "Match", 42, 10),
    AmmoSeed("762x54r", "AP", 38, 15),

    AmmoSeed("12ga", "Buckshot", 45, 3),
    AmmoSeed("12ga", "Slug", 55, 6),
    AmmoSeed("12ga", "Flechette", 40, 8),

    AmmoSeed("338lm", "FMJ", 55, 14),
    AmmoSeed("338lm", "Match", 58, 13),
    AmmoSeed("338lm", "AP", 52, 18),

    AmmoSeed("50bmg", "FMJ", 75, 18),
    AmmoSeed("50bmg", "AP", 70, 24),
    AmmoSeed("50bmg", "API", 72, 22),
]


def ammo_item_name(caliber_code: str, bullet_type: str) -> str:
    cal = CALIBERS.get(caliber_code) or caliber_code
    return f"Патроны {cal} {bullet_type}"


async def main() -> None:
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Нет DB_URL или DATABASE_URL в .env")

    engine = create_async_engine(
        normalize_db_url(db_url),
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        # простая проверка что таблицы есть
        await s.execute(text("SELECT 1 FROM calibers LIMIT 1"))
        await s.execute(text("SELECT 1 FROM weapons LIMIT 1"))
        await s.execute(text(f"SELECT 1 FROM {AMMO_TABLE} LIMIT 1"))

        # calibers
        caliber_id: Dict[str, int] = {}
        for code, name in CALIBERS.items():
            row = (
                await s.execute(
                    text("SELECT id FROM calibers WHERE code = :code"),
                    {"code": code},
                )
            ).first()
            if row:
                cid = int(row[0])
                await s.execute(
                    text("UPDATE calibers SET name = :name WHERE id = :id"),
                    {"name": name, "id": cid},
                )
            else:
                row2 = (
                    await s.execute(
                        text("INSERT INTO calibers (code, name) VALUES (:code, :name) RETURNING id"),
                        {"code": code, "name": name},
                    )
                ).first()
                cid = int(row2[0])
            caliber_id[code] = cid

        # weapons upsert by name
        for w in WEAPONS:
            cid = caliber_id[w.caliber_code]
            existing = (
                await s.execute(
                    text("SELECT id FROM weapons WHERE name = :name"),
                    {"name": w.name},
                )
            ).first()

            if existing:
                await s.execute(
                    text(
                        """
                        UPDATE weapons
                        SET category = :category,
                            caliber_id = :caliber_id,
                            accuracy = :accuracy,
                            reliability = :reliability
                        WHERE name = :name
                        """
                    ),
                    {
                        "name": w.name,
                        "category": w.category,
                        "caliber_id": cid,
                        "accuracy": w.accuracy,
                        "reliability": w.reliability,
                    },
                )
            else:
                await s.execute(
                    text(
                        """
                        INSERT INTO weapons (name, category, caliber_id, accuracy, reliability)
                        VALUES (:name, :category, :caliber_id, :accuracy, :reliability)
                        """
                    ),
                    {
                        "name": w.name,
                        "category": w.category,
                        "caliber_id": cid,
                        "accuracy": w.accuracy,
                        "reliability": w.reliability,
                    },
                )

        # ammo upsert by caliber_id + bullet_type
        for a in AMMO:
            cid = caliber_id[a.caliber_code]
            existing = (
                await s.execute(
                    text(f"SELECT id FROM {AMMO_TABLE} WHERE caliber_id = :cid AND name = :name"),
                    {"cid": cid, "name": a.bullet_type},
                )
            ).first()

            if existing:
                await s.execute(
                    text(
                        f"""
                        UPDATE {AMMO_TABLE}
                        SET damage = :damage,
                            armor_penetration = :ap
                        WHERE caliber_id = :cid AND name = :name
                        """
                    ),
                    {"cid": cid, "name": a.bullet_type, "damage": a.damage, "ap": a.armor_penetration},
                )
            else:
                await s.execute(
                    text(
                        f"""
                        INSERT INTO {AMMO_TABLE} (caliber_id, name, damage, armor_penetration)
                        VALUES (:cid, :name, :damage, :ap)
                        """
                    ),
                    {"cid": cid, "name": a.bullet_type, "damage": a.damage, "ap": a.armor_penetration},
                )

        # ammo items (physical) as обычные предметы в items
        items_available = True
        try:
            await s.execute(text("SELECT 1 FROM items LIMIT 1"))
        except Exception:
            items_available = False

        if items_available:
            for a in AMMO:
                name = ammo_item_name(a.caliber_code, a.bullet_type)
                meta_json = json.dumps(
                    {
                        "kind": "ammo",
                        "caliber_code": a.caliber_code,
                        "bullet_type": a.bullet_type,
                        "damage": a.damage,
                        "armor_penetration": a.armor_penetration,
                    },
                    ensure_ascii=False,
                )

                row_item = (
                    await s.execute(
                        text("SELECT id FROM items WHERE name = :name"),
                        {"name": name},
                    )
                ).first()

                if row_item:
                    await s.execute(
                        text(
                            """
                            UPDATE items
                            SET item_type = 'misc',
                                meta_json = (:meta_json)::jsonb,
                                weight = 0,
                                price = 0,
                                loot_type = 'common'
                            WHERE id = :id
                            """
                        ),
                        {"id": int(row_item[0]), "meta_json": meta_json},
                    )
                else:
                    await s.execute(
                        text(
                            """
                            INSERT INTO items (item_type, name, meta_json, weight, price, loot_type)
                            VALUES ('misc', :name, (:meta_json)::jsonb, 0, 0, 'common')
                            """
                        ),
                        {"name": name, "meta_json": meta_json},
                    )

        await s.commit()

    await engine.dispose()
    print(f"OK: weapons={len(WEAPONS)} ammo={len(AMMO)} calibers={len(CALIBERS)}")


if __name__ == "__main__":
    asyncio.run(main())
