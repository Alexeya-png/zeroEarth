# scripts/render_health_overlay.py
# pip install pillow

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from io import BytesIO

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Injuries:
    head: int
    torso: int
    arm: int
    leg: int

ф
def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_path(p: str) -> Path:
    x = Path(p)
    if x.exists():
        return x

    y = _project_root() / "assets" / p
    if y.exists():
        return y

    raise FileNotFoundError(f"No such file: {p} (also tried {y})")


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
    lost_ratio = max(0.0, min(1.0, float(lost_ratio)))
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
    s = int(size)
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

    for key, lvl in (
        ("head", injuries.head),
        ("torso", injuries.torso),
        ("arm", injuries.arm),
        ("leg", injuries.leg),
    ):
        if int(lvl) <= 0:
            continue
        rx, ry = pts[key]
        cx, cy = int(w * rx), int(h * ry)
        draw_x(d, cx, cy, size_by_lvl(int(lvl)), color)


def _render_health_overlay_image(
    *,
    inp: str,
    max_hp: int,
    current_hp: int,
    injuries: Injuries,
    bg: Optional[str] = None,
    threshold: int = 120,
) -> Image.Image:
    max_hp_i = max(1, int(max_hp))
    current_hp_i = max(0, min(max_hp_i, int(current_hp)))

    src = Image.open(resolve_path(inp)).convert("RGBA")
    mask = remove_floor_from_mask(make_mask(src, threshold))
    overlay = cutout(src, mask)

    lost_ratio = (max_hp_i - current_hp_i) / max_hp_i
    draw_fill(overlay, mask, lost_ratio)
    draw_injuries(overlay, injuries)

    if bg:
        bg_img = Image.open(resolve_path(bg)).convert("RGBA")
        if bg_img.size != overlay.size:
            bg_img = bg_img.resize(overlay.size, Image.Resampling.LANCZOS)
        out_img = bg_img.copy()
        out_img.alpha_composite(overlay)
        return out_img

    return overlay


def render_health_overlay_bytes(
    *,
    inp: str,
    max_hp: int,
    current_hp: int,
    injuries: Injuries,
    bg: Optional[str] = None,
    threshold: int = 120,
) -> bytes:
    img = _render_health_overlay_image(
        inp=inp,
        bg=bg,
        max_hp=max_hp,
        current_hp=current_hp,
        injuries=injuries,
        threshold=threshold,
    )
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_health_overlay(
    *,
    inp: str,
    out: str,
    max_hp: int,
    current_hp: int,
    injuries: Injuries,
    bg: Optional[str] = None,
    threshold: int = 120,
) -> None:
    img = _render_health_overlay_image(
        inp=inp,
        bg=bg,
        max_hp=max_hp,
        current_hp=current_hp,
        injuries=injuries,
        threshold=threshold,
    )
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True, help="body image (png with alpha)")
    p.add_argument("--bg", dest="bg", default=None, help="background image (art.png)")
    p.add_argument("--out", dest="out", required=True, help="output png")
    p.add_argument("--max-hp", type=int, required=True, help="max HP")
    p.add_argument("--current-hp", type=int, required=True, help="current HP")
    p.add_argument("--head-injury", type=int, default=0)
    p.add_argument("--torso-injury", type=int, default=0)
    p.add_argument("--arm-injury", type=int, default=0)
    p.add_argument("--leg-injury", type=int, default=0)
    p.add_argument("--threshold", type=int, default=120, help="mask threshold if no transparency")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    render_health_overlay(
        inp=a.inp,
        bg=a.bg,
        out=a.out,
        max_hp=a.max_hp,
        current_hp=a.current_hp,
        injuries=Injuries(
            head=a.head_injury,
            torso=a.torso_injury,
            arm=a.arm_injury,
            leg=a.leg_injury,
        ),
        threshold=a.threshold,
    )


if __name__ == "__main__":
    main()
