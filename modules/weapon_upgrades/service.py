from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


@dataclass(frozen=True)
class WeaponRow:
    id: int
    name: str
    category: str
    caliber_id: int
    accuracy: int
    reliability: int
    weight_kg: float
    price: int


@dataclass(frozen=True)
class AmmoRow:
    name: str
    damage: int
    armor_penetration: int


@dataclass(frozen=True)
class UniqueWeaponInfo:
    base_weapon_id: int
    mods: list[dict[str, Any]]
    total_bonus: dict[str, int]


@dataclass(frozen=True)
class InventoryMod:
    item_id: int
    qty: int
    name: str
    weight: float
    price: int
    mod_type: str
    tier: str
    compatible_categories: list[str]
    unique_weapon_id: Optional[int]
    accuracy_bonus: int
    reliability_bonus: int
    damage_bonus: int
    armor_pen_bonus: int
    loot_analysis_bonus: int

    def is_compatible(
        self,
        weapon_category: str,
        base_weapon_id: int,
        installed_types: set[str] | None = None,
    ) -> bool:
        if self.unique_weapon_id is not None and self.unique_weapon_id != base_weapon_id:
            return False

        if weapon_category not in (self.compatible_categories or []):
            return False

        if installed_types is not None:
            it = {str(x).lower() for x in installed_types}
            mt = str(self.mod_type or "").lower()
            if mt in ("optic", "scope") and "rail" not in it:
                return False

        return True

    def to_json(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "mod_type": self.mod_type,
            "tier": self.tier,
            "accuracy_bonus": self.accuracy_bonus,
            "reliability_bonus": self.reliability_bonus,
            "damage_bonus": self.damage_bonus,
            "armor_pen_bonus": self.armor_pen_bonus,
            "loot_analysis_bonus": self.loot_analysis_bonus,
        }


class WeaponUpgradeService:
    """Улучшение оружия.

    Логика:
    - Моды – это предметы (items) + записи в weapon_mods.
    - Моды лежат в character_inventory.
    - Установленный мод создаёт НОВУЮ запись в weapons и запись в weapon_uniques.
    - Мод расходуется (qty - 1). Снять нельзя.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _ensure_not_in_active_raid(self, character_id: int) -> None:
        row = (
            await self.session.execute(
                text("SELECT 1 FROM raids WHERE character_id = :cid AND status = 'active' LIMIT 1"),
                {"cid": int(character_id)},
            )
        ).first()
        if row:
            raise ValueError("Персонаж в рейде. Улучшение оружия недоступно")

    async def _get_inventory_qty(self, character_id: int, item_id: int) -> int:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT qty
                    FROM character_inventory
                    WHERE character_id = :cid AND item_id = :iid
                    """
                ),
                {"cid": character_id, "iid": item_id},
            )
        ).mappings().first()
        if not r or r.get("qty") is None:
            return 0
        try:
            return int(r["qty"])
        except Exception:
            return 0

    async def _dec_inventory(self, character_id: int, item_id: int, dec: int) -> None:
        if dec <= 0:
            return
        await self.session.execute(
            text(
                """
                UPDATE character_inventory
                SET qty = GREATEST(qty - :dec, 0)
                WHERE character_id = :cid AND item_id = :iid
                """
            ),
            {"cid": character_id, "iid": item_id, "dec": int(dec)},
        )
        await self.session.execute(
            text(
                """
                DELETE FROM character_inventory
                WHERE character_id = :cid AND item_id = :iid AND qty <= 0
                """
            ),
            {"cid": character_id, "iid": item_id},
        )

    async def get_weapon_id_in_slot(self, character_id: int, slot: int) -> Optional[int]:
        if slot not in (1, 2, 3):
            return None

        r = (
            await self.session.execute(
                text(
                    """
                    SELECT weapon_1_id, weapon_2_id, weapon_3_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().first()
        if not r:
            return None

        key = f"weapon_{slot}_id"
        wid = r.get(key)
        return int(wid) if wid else None

    async def get_equipped_weapons(self, character_id: int) -> dict[int, WeaponRow]:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT weapon_1_id, weapon_2_id, weapon_3_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().first()

        if not r:
            return {}

        out: dict[int, WeaponRow] = {}
        for slot, key in ((1, "weapon_1_id"), (2, "weapon_2_id"), (3, "weapon_3_id")):
            wid = r.get(key)
            if not wid:
                continue
            w = await self.get_weapon(int(wid))
            if w:
                out[slot] = w
        return out

    async def get_weapon(self, weapon_id: int) -> Optional[WeaponRow]:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT id, name, category, caliber_id, accuracy, reliability,
                           COALESCE(weight_kg, 0) AS weight_kg,
                           COALESCE(price, 0) AS price
                    FROM weapons
                    WHERE id = :wid
                    """
                ),
                {"wid": weapon_id},
            )
        ).mappings().first()
        if not r:
            return None
        return WeaponRow(
            id=int(r["id"]),
            name=str(r["name"]),
            category=str(r["category"]),
            caliber_id=int(r["caliber_id"]),
            accuracy=int(r["accuracy"]),
            reliability=int(r["reliability"]),
            weight_kg=float(r["weight_kg"] or 0),
            price=int(r["price"] or 0),
        )

    async def get_best_ammo_for_caliber(self, caliber_id: int) -> Optional[AmmoRow]:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT name, damage, armor_penetration
                    FROM ammo_types
                    WHERE caliber_id = :cid
                    ORDER BY damage DESC, armor_penetration DESC
                    LIMIT 1
                    """
                ),
                {"cid": caliber_id},
            )
        ).mappings().first()
        if not r:
            return None
        return AmmoRow(
            name=str(r["name"]),
            damage=int(r["damage"]),
            armor_penetration=int(r["armor_penetration"]),
        )

    async def get_unique_info(self, weapon_id: int) -> Optional[UniqueWeaponInfo]:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT base_weapon_id,
                           COALESCE(mods_json, '[]'::jsonb) AS mods_json,
                           COALESCE(total_bonus_json, '{}'::jsonb) AS total_bonus_json
                    FROM weapon_uniques
                    WHERE weapon_id = :wid
                    """
                ),
                {"wid": weapon_id},
            )
        ).mappings().first()

        if not r:
            return None

        mods = list(r["mods_json"] or [])
        tb = dict(r["total_bonus_json"] or {})
        total_bonus = {
            "accuracy_bonus": int(tb.get("accuracy_bonus", 0) or 0),
            "reliability_bonus": int(tb.get("reliability_bonus", 0) or 0),
            "damage_bonus": int(tb.get("damage_bonus", 0) or 0),
            "armor_pen_bonus": int(tb.get("armor_pen_bonus", 0) or 0),
            "loot_analysis_bonus": int(tb.get("loot_analysis_bonus", 0) or 0),
        }

        return UniqueWeaponInfo(
            base_weapon_id=int(r["base_weapon_id"]),
            mods=mods,
            total_bonus=total_bonus,
        )

    async def list_inventory_mods(self, character_id: int) -> list[InventoryMod]:
        res = await self.session.execute(
            text(
                """
                SELECT
                  ci.item_id,
                  ci.qty,
                  i.name,
                  COALESCE(i.weight, 0) AS weight,
                  COALESCE(i.price, 0) AS price,
                  wm.mod_type,
                  wm.tier,
                  wm.compatible_categories,
                  wm.unique_weapon_id,
                  wm.accuracy_bonus,
                  wm.reliability_bonus,
                  wm.damage_bonus,
                  wm.armor_pen_bonus,
                  COALESCE(
                    NULLIF(wm.meta_json->>'loot_analysis_bonus','')::int,
                    NULLIF(wm.meta_json->>'lootAnalysisBonus','')::int,
                    0
                  ) AS loot_analysis_bonus
                FROM character_inventory ci
                JOIN items i ON i.id = ci.item_id
                JOIN weapon_mods wm ON wm.item_id = ci.item_id
                WHERE ci.character_id = :cid AND ci.qty > 0
                ORDER BY wm.tier DESC, wm.mod_type, i.name
                """
            ),
            {"cid": character_id},
        )

        out: list[InventoryMod] = []
        for r in res.mappings().all():
            out.append(
                InventoryMod(
                    item_id=int(r["item_id"]),
                    qty=int(r["qty"]),
                    name=str(r["name"]),
                    weight=float(r["weight"] or 0),
                    price=int(r["price"] or 0),
                    mod_type=str(r["mod_type"]),
                    tier=str(r["tier"]),
                    compatible_categories=list(r["compatible_categories"] or []),
                    unique_weapon_id=int(r["unique_weapon_id"]) if r["unique_weapon_id"] is not None else None,
                    accuracy_bonus=int(r["accuracy_bonus"] or 0),
                    reliability_bonus=int(r["reliability_bonus"] or 0),
                    damage_bonus=int(r["damage_bonus"] or 0),
                    armor_pen_bonus=int(r["armor_pen_bonus"] or 0),
                    loot_analysis_bonus=int(r["loot_analysis_bonus"] or 0),
                )
            )
        return out

    async def _get_mod_in_inventory(self, character_id: int, item_id: int) -> Optional[InventoryMod]:
        r = (
            await self.session.execute(
                text(
                    """
                    SELECT
                      ci.item_id,
                      ci.qty,
                      i.name,
                      COALESCE(i.weight, 0) AS weight,
                      COALESCE(i.price, 0) AS price,
                      wm.mod_type,
                      wm.tier,
                      wm.compatible_categories,
                      wm.unique_weapon_id,
                      wm.accuracy_bonus,
                      wm.reliability_bonus,
                      wm.damage_bonus,
                      wm.armor_pen_bonus,
                      COALESCE(
                        NULLIF(wm.meta_json->>'loot_analysis_bonus','')::int,
                        NULLIF(wm.meta_json->>'lootAnalysisBonus','')::int,
                        0
                      ) AS loot_analysis_bonus
                    FROM character_inventory ci
                    JOIN items i ON i.id = ci.item_id
                    JOIN weapon_mods wm ON wm.item_id = ci.item_id
                    WHERE ci.character_id = :cid AND ci.item_id = :iid AND ci.qty > 0
                    """
                ),
                {"cid": character_id, "iid": item_id},
            )
        ).mappings().first()

        if not r:
            return None

        return InventoryMod(
            item_id=int(r["item_id"]),
            qty=int(r["qty"]),
            name=str(r["name"]),
            weight=float(r["weight"] or 0),
            price=int(r["price"] or 0),
            mod_type=str(r["mod_type"]),
            tier=str(r["tier"]),
            compatible_categories=list(r["compatible_categories"] or []),
            unique_weapon_id=int(r["unique_weapon_id"]) if r["unique_weapon_id"] is not None else None,
            accuracy_bonus=int(r["accuracy_bonus"] or 0),
            reliability_bonus=int(r["reliability_bonus"] or 0),
            damage_bonus=int(r["damage_bonus"] or 0),
            armor_pen_bonus=int(r["armor_pen_bonus"] or 0),
            loot_analysis_bonus=int(r["loot_analysis_bonus"] or 0),
        )

    _MODTYPE_LABEL: dict[str, str] = {
        "suppressor": "глушителем",
        "muzzle": "дульным устройством",
        "optic": "коллиматором",
        "scope": "оптикой",
        "rail": "планкой",
        "grip": "рукоятью",
        "stock": "прикладом",
        "handguard": "цевьём",
        "magazine": "магазином",
        "trigger": "УСМ",
        "barrel": "стволом",
        "gas": "газблоком",
        "bolt": "затворной группой",
        "bipod": "сошками",
        "light": "фонарём",
        "laser": "ЛЦУ",
        "choke": "чоком",
        "tube": "трубкой",
        "tactical": "тактическим модулем",
    }

    _MODTYPE_SHORT: dict[str, str] = {
        "suppressor": "глушитель",
        "muzzle": "ДТК",
        "optic": "коллиматор",
        "scope": "оптика",
        "rail": "планка",
        "grip": "рукоять",
        "stock": "приклад",
        "handguard": "цевьё",
        "magazine": "магазин",
        "trigger": "УСМ",
        "barrel": "ствол",
        "gas": "газ",
        "bolt": "затвор",
        "bipod": "сошки",
        "light": "фонарь",
        "laser": "ЛЦУ",
        "choke": "чок",
        "tube": "трубка",
        "tactical": "тактика",
    }

    _MODTYPE_PRIORITY: dict[str, int] = {
        "scope": 10,
        "optic": 11,
        "suppressor": 12,
        "muzzle": 13,
        "barrel": 14,
        "trigger": 15,
        "bolt": 16,
        "stock": 17,
        "grip": 18,
        "handguard": 19,
        "magazine": 20,
        "rail": 21,
        "laser": 22,
        "light": 23,
        "bipod": 24,
        "gas": 25,
        "choke": 26,
        "tube": 27,
        "tactical": 28,
    }

    @staticmethod
    def _tier_rank(tier: str) -> int:
        return {"D": 1, "C": 2, "B": 3, "A": 4, "S": 5}.get((tier or "").upper(), 0)

    _IGNORE_IN_NAMING: set[str] = {"rail"}

    def _generate_unique_weapon_name(
        self,
        base_name: str,
        mods: list[dict[str, Any]],
        last_mod_name: str | None = None,
    ) -> str:
        affecting = [
            m for m in (mods or [])
            if str(m.get("mod_type", "")).lower() not in self._IGNORE_IN_NAMING
        ]

        if not affecting:
            return base_name[:128]

        mods_sorted = sorted(affecting, key=lambda m: self._MODTYPE_PRIORITY.get(str(m.get("mod_type")), 999))
        n = len(mods_sorted)

        max_tier = 0
        for m in mods_sorted:
            max_tier = max(max_tier, self._tier_rank(str(m.get("tier", ""))))

        if n == 1:
            mt = str(mods_sorted[0].get("mod_type"))
            label = self._MODTYPE_LABEL.get(mt, mt)
            res = f"{base_name} с {label}"
            return res[:128]

        if max_tier >= 5 and n >= 2:
            prefix = "Элитный"
        elif max_tier >= 4 and n >= 3:
            prefix = "Продвинутый"
        elif n == 2:
            prefix = "Тактический"
        elif n == 3:
            prefix = "Улучшенный"
        elif n == 4:
            prefix = "Модифицированный"
        else:
            prefix = "Экспериментальный"

        res = f"{prefix} {base_name}"
        return res[:128]
    async def _item_name_exists(self, item_type: str, name: str) -> bool:
        row = (
            await self.session.execute(
                text(
                    """
                    SELECT 1
                    FROM items
                    WHERE item_type = :t AND name = :n
                    LIMIT 1
                    """
                ),
                {"t": item_type, "n": name},
            )
        ).first()
        return bool(row)

    async def _make_unique_item_name(self, item_type: str, desired: str, max_tries: int = 80) -> str:
        name = (desired or "")[:128] or "item"
        zws = "\u200B"  # zero width space

        for _ in range(max_tries):
            if not await self._item_name_exists(item_type, name):
                return name

            if len(name) >= 128:
                name = name[:127] + zws
            else:
                name = name + zws

        raise ValueError("Не удалось подобрать уникальное имя предмета")

    async def apply_mod(
        self,
        character_id: int,
        slot: int,
        expected_weapon_id: int,
        mod_item_id: int,
    ) -> WeaponRow:
        if slot not in (1, 2, 3):
            raise ValueError("Неверный слот")

        await self._ensure_not_in_active_raid(character_id)

        eq = (
            await self.session.execute(
                text(
                    """
                    SELECT weapon_1_id, weapon_2_id, weapon_3_id
                    FROM equipment
                    WHERE character_id = :cid
                    """
                ),
                {"cid": character_id},
            )
        ).mappings().first()

        if not eq:
            raise ValueError("Экипировка не найдена")

        key = f"weapon_{slot}_id"
        wid = eq.get(key)
        if not wid:
            raise ValueError("В этом слоте нет оружия")

        current_weapon_id = int(wid)
        if current_weapon_id != expected_weapon_id:
            raise ValueError("Оружие в слоте изменилось. Открой улучшение заново")

        qty_before_current = await self._get_inventory_qty(character_id, current_weapon_id)

        weapon = await self.get_weapon(current_weapon_id)
        if not weapon:
            raise ValueError("Оружие не найдено")

        unique = await self.get_unique_info(current_weapon_id)
        base_weapon_id = unique.base_weapon_id if unique else weapon.id
        existing_mods = list(unique.mods) if unique else []
        totals = dict(unique.total_bonus) if unique else {
            "accuracy_bonus": 0,
            "reliability_bonus": 0,
            "damage_bonus": 0,
            "armor_pen_bonus": 0,
            "loot_analysis_bonus": 0,
        }

        qty_before_base = 0
        if base_weapon_id != current_weapon_id:
            qty_before_base = await self._get_inventory_qty(character_id, base_weapon_id)

        mod = await self._get_mod_in_inventory(character_id, mod_item_id)
        if not mod:
            raise ValueError("Мод не найден в рюкзаке")

        installed_types = {str(m.get("mod_type", "")).lower() for m in existing_mods}

        if mod.mod_type in ("optic", "scope") and "rail" not in installed_types:
            raise ValueError("Для установки оптики нужна планка")

        if not mod.is_compatible(weapon.category, base_weapon_id, installed_types=installed_types):
            raise ValueError("Этот мод не подходит к этому оружию")

        if any(str(m.get("mod_type")).lower() == str(mod.mod_type).lower() for m in existing_mods):
            raise ValueError("Мод этого типа уже установлен")

        new_mods = existing_mods + [mod.to_json()]
        new_totals = {
            "accuracy_bonus": int(totals.get("accuracy_bonus", 0)) + mod.accuracy_bonus,
            "reliability_bonus": int(totals.get("reliability_bonus", 0)) + mod.reliability_bonus,
            "damage_bonus": int(totals.get("damage_bonus", 0)) + mod.damage_bonus,
            "armor_pen_bonus": int(totals.get("armor_pen_bonus", 0)) + mod.armor_pen_bonus,
            "loot_analysis_bonus": int(totals.get("loot_analysis_bonus", 0)) + mod.loot_analysis_bonus,
        }

        new_accuracy = _clamp(weapon.accuracy + mod.accuracy_bonus, 50, 100)
        new_reliability = _clamp(weapon.reliability + mod.reliability_bonus, 0, 100)
        new_weight = float(weapon.weight_kg) + float(mod.weight)
        new_price = int(weapon.price) + int(mod.price)

        base_weapon = await self.get_weapon(base_weapon_id)
        base_name = base_weapon.name if base_weapon else weapon.name
        new_name = self._generate_unique_weapon_name(base_name=base_name, mods=new_mods, last_mod_name=mod.name)
        new_name = await self._make_unique_item_name("weapon", new_name)

        meta = {
            "kind": "unique_weapon",
            "base_weapon_id": base_weapon_id,
            "parent_weapon_id": current_weapon_id,
            "mods": new_mods,
            "total_bonus": new_totals,
        }

        ins = (
            await self.session.execute(
                text(
                    """
                    INSERT INTO items (
                      item_type, name, meta_json,
                      accuracy, reliability,
                      weight, weight_kg, price,
                      caliber_id, category,
                      quality_score, quality_tier, loot_type
                    )
                    SELECT
                      'weapon',
                      :name,
                      CAST(:meta AS jsonb),
                      :acc,
                      :rel,
                      :weight,
                      :weight,
                      :price,
                      i.caliber_id,
                      i.category,
                      i.quality_score,
                      i.quality_tier,
                      i.loot_type
                    FROM items i
                    WHERE i.id = :src_id
                    RETURNING id
                    """
                ),
                {
                    "name": new_name,
                    "acc": new_accuracy,
                    "rel": new_reliability,
                    "weight": new_weight,
                    "price": new_price,
                    "meta": json.dumps(meta, ensure_ascii=False),
                    "src_id": current_weapon_id,
                },
            )
        ).mappings().first()

        if not ins:
            raise ValueError("Не удалось создать оружие")
        new_weapon_id = int(ins["id"])

        await self.session.execute(
            text(
                """
                INSERT INTO weapon_uniques (
                  weapon_id, base_weapon_id, parent_weapon_id, character_id,
                  mods_json, total_bonus_json
                )
                VALUES (
                  :wid, :base, :parent, :cid,
                  CAST(:mods AS jsonb), CAST(:tb AS jsonb)
                )
                """
            ),
            {
                "wid": new_weapon_id,
                "base": base_weapon_id,
                "parent": current_weapon_id,
                "cid": character_id,
                "mods": json.dumps(new_mods, ensure_ascii=False),
                "tb": json.dumps(new_totals, ensure_ascii=False),
            },
        )

        if slot == 1:
            await self.session.execute(
                text("UPDATE equipment SET weapon_1_id = :wid WHERE character_id = :cid"),
                {"wid": new_weapon_id, "cid": character_id},
            )
        elif slot == 2:
            await self.session.execute(
                text("UPDATE equipment SET weapon_2_id = :wid WHERE character_id = :cid"),
                {"wid": new_weapon_id, "cid": character_id},
            )
        else:
            await self.session.execute(
                text("UPDATE equipment SET weapon_3_id = :wid WHERE character_id = :cid"),
                {"wid": new_weapon_id, "cid": character_id},
            )

        qty_after_current = await self._get_inventory_qty(character_id, current_weapon_id)
        target_current = max(qty_before_current - 1, 0)
        if qty_after_current > target_current:
            await self._dec_inventory(character_id, current_weapon_id, qty_after_current - target_current)

        if base_weapon_id != current_weapon_id:
            qty_after_base = await self._get_inventory_qty(character_id, base_weapon_id)
            if qty_after_base > qty_before_base:
                await self._dec_inventory(character_id, base_weapon_id, qty_after_base - qty_before_base)

        await self._dec_inventory(character_id, mod_item_id, 1)

        new_weapon = await self.get_weapon(new_weapon_id)
        if not new_weapon:
            raise ValueError("Созданное оружие не найдено")
        return new_weapon
