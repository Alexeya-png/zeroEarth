const tg = window.Telegram?.WebApp;
tg?.ready();

function getStartParam() {
  const qs = new URLSearchParams(window.location.search);
  return (
    tg?.initDataUnsafe?.start_param ||
    qs.get("tgWebAppStartParam") ||
    ""
  );
}

function fmtNum(v) {
  if (v === null || v === undefined) return "—";
  return String(v);
}

function fmtKg(v) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return `${n} кг`;
}

function linesFromItem(item) {
  const lines = [];

  lines.push(`ID: ${item.id}`);
  lines.push(`Тип: ${item.item_type}`);
  lines.push(`Лут: ${item.loot_type}`);
  lines.push(`Вес: ${fmtKg(item.weight_kg)}`);
  lines.push(`Цена: ${fmtNum(item.price)}`);

  if (item.caliber) {
    lines.push(`Калибр: ${item.caliber.code || ""} ${item.caliber.name || ""}`.trim());
  }

  if (item.accuracy !== null && item.accuracy !== undefined) lines.push(`Точность: ${item.accuracy}`);
  if (item.reliability !== null && item.reliability !== undefined) lines.push(`Надёжность: ${item.reliability}`);

  if (item.quality_tier) lines.push(`Quality tier: ${item.quality_tier}`);
  if (item.quality_score !== null && item.quality_score !== undefined) lines.push(`Quality score: ${item.quality_score}`);

  if (item.equipment_stats) {
    lines.push("");
    lines.push("Экип. статы:");
    lines.push(`Tier: ${item.equipment_stats.tier}`);
    lines.push(`Armor: ${item.equipment_stats.armor}`);
    lines.push(`Reliability: ${item.equipment_stats.reliability}`);
    lines.push(`Accuracy bonus: ${item.equipment_stats.accuracy_bonus}`);
    lines.push(`Reaction bonus: ${item.equipment_stats.reaction_bonus}`);
    lines.push(`Initiative bonus: ${item.equipment_stats.initiative_bonus}`);
    lines.push(`Stealth bonus: ${item.equipment_stats.stealth_bonus}`);
    lines.push(`Carry cap bonus: ${item.equipment_stats.carry_capacity_bonus}`);
    lines.push(`Loot analysis bonus: ${item.equipment_stats.loot_analysis_bonus}`);
    lines.push(`Item handling bonus: ${item.equipment_stats.item_handling_bonus}`);
  }

  if (item.weapon_mod) {
    lines.push("");
    lines.push("Мод оружия:");
    lines.push(`Тип: ${item.weapon_mod.mod_type}`);
    lines.push(`Tier: ${item.weapon_mod.tier}`);
    lines.push(`Slot limit: ${item.weapon_mod.slot_limit}`);
    lines.push(`Accuracy bonus: ${item.weapon_mod.accuracy_bonus}`);
    lines.push(`Reliability bonus: ${item.weapon_mod.reliability_bonus}`);
    lines.push(`Damage bonus: ${item.weapon_mod.damage_bonus}`);
    lines.push(`Armor pen bonus: ${item.weapon_mod.armor_pen_bonus}`);
  }

  return lines.join("\n");
}

async function main() {
  const title = document.getElementById("title");
  const sub = document.getElementById("sub");
  const details = document.getElementById("details");

  const sp = getStartParam();
  const m = /^i(\d+)$/.exec(sp);

  if (!m) {
    title.textContent = "Нет item_id";
    sub.textContent = "Открой через ссылку из склада";
    return;
  }

  const id = m[1];
  title.textContent = "Загрузка…";
  sub.textContent = `item_id = ${id}`;

  const r = await fetch(`/api/item?id=${encodeURIComponent(id)}`);
  const j = await r.json();

  if (!j.ok) {
    title.textContent = "Ошибка";
    details.textContent = j.error || "unknown";
    return;
  }

  const item = j.item;
  title.textContent = item.name;
  details.textContent = linesFromItem(item);
}

main().catch((e) => {
  document.getElementById("title").фtextContent = "Ошибка";
  document.getElementById("details").textContent = String(e);
});