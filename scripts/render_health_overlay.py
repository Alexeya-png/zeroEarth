# scripts/render_health_overlay.py
# pip install pillow psycopg2-binary python-dotenv

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Injuries:
    head: int
    torso: int
    arm: int
    leg: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True, help="body image (png with alpha)")
    p.add_argument("--bg", dest="bg", default=None, help="background image (art.png)")
    p.add_argument("--out", dest="out", required=True, help="output png")
    p.add_argument("--max-hp", type=int, default=None, help="override max hp (optional)")
    p.add_argument("--threshold", type=int, default=120, help="mask threshold if no transparency")

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--character-id", type=int, help="character id in DB")
    g.add_argument("--tg-id", type=int, help="telegram user id, will pick latest character")

    return p.parse_args()


def resolve_path(p: str) -> Path:
    x = Path(p)
    if x.exists():
        return x
    y = Path("assets") / p
    if y.exists():
        return y
    raise FileNotFoundError(f"No such file: {p} (also tried assets/{p})")


def get_conn_kwargs_from_env() -> dict:
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    dbname = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")

    missing = [k for k, v in (("DB_HOST", host), ("DB_PORT", port), ("DB_NAME", dbname), ("DB_USER", user), ("DB_PASSWORD", password)) if not v]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")

    try:
        port_i = int(str(port))
    except Exception:
        raise SystemExit(f"DB_PORT must be int, got: {port!r}")

    return {
        "host": host,
        "port": port_i,
        "dbname": dbname,
        "user": user,
        "password": password,
        "sslmode": "require",
    }


def fetch_character_state(conn_kwargs: dict, character_id: int | None, tg_id: int | None) -> tuple[int, int, Injuries]:
    with psycopg2.connect(**conn_kwargs) as conn:
        with conn.cursor() as cur:
            if character_id is None:
                cur.execute(
                    """
                    SELECT c.id
                    FROM users u
                    JOIN characters c ON c.user_id = u.id
                    WHERE u.tg_id = %s
                    ORDER BY c.created_at DESC, c.id DESC
                    LIMIT 1
                    """,
                    (tg_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise SystemExit("No characters for this tg-id")
                character_id = int(row[0])

            cur.execute(
                """
                SELECT
                  c.hp AS max_hp,
                  COALESCE(ch.current_hp, c.hp) AS current_hp,
                  COALESCE(ch.head_injury, 0) AS head_injury,
                  COALESCE(ch.torso_injury, 0) AS torso_injury,
                  COALESCE(ch.arm_injury, 0) AS arm_injury,
                  COALESCE(ch.leg_injury, 0) AS leg_injury
                FROM characters c
                LEFT JOIN character_health ch ON ch.character_id = c.id
                WHERE c.id = %s
                """,
                (character_id,),
            )
            row = cur.fetchone()
            if not row:
                raise SystemExit("Character not found")

            max_hp = int(row[0])
            current_hp = int(row[1])
            inj = Injuries(int(row[2]), int(row[3]), int(row[4]), int(row[5]))
            current_hp = max(0, min(max_hp, current_hp))
            return max_hp, current_hp, inj


def make_mask(img_rgba: Image.Image, threshold: int) -> Image.Image:
    alpha = img_rgba.getchannel("A")
    if alpha.getextrema() != (255, 255):
        return alpha
    gray = img_rgba.convert("L")
    return gray.point(lambda v: 255 if v < threshold else 0).convert("L")


def remove_floor_from_mask(mask: Image.Image) -> Image.Image:
    w, h = mask.size
    px = mask.load()

    counts = [0] * h
    for y in range(h):
        c = 0
        for x in range(w):
            if px[x, y] > 0:
                c += 1
        counts[y] = c

    top_h = int(h * 0.70)
    core = [c for c in counts[:top_h] if c > 0]
    if not core:
        return mask

    core.sort()
    median = core[len(core) // 2]
    if median <= 0:
        return mask

    cut_y = None
    spike = int(median * 2.2)
    for y in range(int(h * 0.55), h):
        if counts[y] >= spike:
            cut_y = y
            break

    if cut_y is None:
        return mask

    out = mask.copy()
    d = ImageDraw.Draw(out)
    d.rectangle([0, cut_y, w, h], fill=0)
    return out


def cutout(img_rgba: Image.Image, mask: Image.Image) -> Image.Image:
    out = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    out.paste(img_rgba, (0, 0), mask)
    return out


def draw_fill(base: Image.Image, mask: Image.Image, lost_ratio: float) -> None:
    w, h = base.size
    lost_ratio = max(0.0, min(1.0, lost_ratio))
    fill_h = int(h * lost_ratio)
    if fill_h <= 0:
        return

    y0 = h - fill_h

    fill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(fill)

    for y in range(y0, h):
        t = (y - y0) / max(1, (h - y0 - 1))
        a = int(90 + 140 * t)
        d.line([(0, y), (w, y)], fill=(220, 30, 30, a), width=1)

    clipped = Image.composite(fill, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask)
    base.alpha_composite(clipped)


def draw_x(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple[int, int, int, int]) -> None:
    s = size
    w = max(2, s // 4)
    draw.line([(cx - s, cy - s), (cx + s, cy + s)], fill=color, width=w)
    draw.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def draw_injuries(base: Image.Image, injuries: Injuries) -> None:
    w, h = base.size
    d = ImageDraw.Draw(base)

    pts = {
        "head": (0.50, 0.14),
        "torso": (0.55, 0.28),
        "arm": (0.67, 0.38),
        "leg": (0.60, 0.58),
    }

    def size_by_lvl(lvl: int) -> int:
        base_s = max(12, w // 40)
        return base_s + (lvl - 1) * max(6, w // 120)

    color = (220, 30, 30, 255)

    for key, lvl in (("head", injuries.head), ("torso", injuries.torso), ("arm", injuries.arm), ("leg", injuries.leg)):
        if lvl <= 0:
            continue
        rx, ry = pts[key]
        cx, cy = int(w * rx), int(h * ry)
        draw_x(d, cx, cy, size_by_lvl(lvl), color)


def main() -> None:
    load_dotenv()

    a = parse_args()
    conn_kwargs = get_conn_kwargs_from_env()

    max_hp, current_hp, injuries = fetch_character_state(conn_kwargs, a.character_id, a.tg_id)
    if a.max_hp is not None:
        max_hp = max(1, int(a.max_hp))
        current_hp = max(0, min(max_hp, current_hp))

    src = Image.open(resolve_path(a.inp)).convert("RGBA")
    mask = remove_floor_from_mask(make_mask(src, a.threshold))
    overlay = cutout(src, mask)

    lost_ratio = (max_hp - current_hp) / max_hp
    draw_fill(overlay, mask, lost_ratio)
    draw_injuries(overlay, injuries)

    if a.bg:
        bg = Image.open(resolve_path(a.bg)).convert("RGBA")
        if bg.size != overlay.size:
            bg = bg.resize(overlay.size, Image.Resampling.LANCZOS)
        out_img = bg.copy()
        out_img.alpha_composite(overlay)
    else:
        out_img = overlay

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path, "PNG")


if __name__ == "__main__":
    main()
