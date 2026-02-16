# scripts/png_bg_resize_tool.py
from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageTk, ImageChops, ImageFilter, ImageDraw


BG_COLORS = ["#97c9d3", "#9ba2cf", "#c89bcf", "#eba0b3", "#d9df8b"]
SPLIT_MODES = ["Нет", "2 кадра – горизонтально", "2 кадра – вертикально"]


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def dist2(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def min_dist2(rgb: Tuple[int, int, int], palette: List[Tuple[int, int, int]]) -> int:
    m = 10**18
    for p in palette:
        d = dist2(rgb, p)
        if d < m:
            m = d
    return m


def _quantize_rgb(rgb: Tuple[int, int, int], step: int = 16) -> Tuple[int, int, int]:
    return tuple(min(255, int(round(c / step) * step)) for c in rgb)


def _border_samples(img_rgb: Image.Image, step: int) -> List[Tuple[int, int, int]]:
    w, h = img_rgb.size
    px = img_rgb.load()
    s: List[Tuple[int, int, int]] = []

    for x in range(0, w, step):
        s.append(px[x, 0])
        s.append(px[x, h - 1])
    for y in range(0, h, step):
        s.append(px[0, y])
        s.append(px[w - 1, y])

    s.append(px[0, 0])
    s.append(px[w - 1, 0])
    s.append(px[0, h - 1])
    s.append(px[w - 1, h - 1])
    return s


def detect_bg_palette(img_rgb: Image.Image, k: int = 3) -> List[Tuple[int, int, int]]:
    w, h = img_rgb.size
    step = max(4, min(24, max(w, h) // 120))
    samples = _border_samples(img_rgb, step=step)
    q = [_quantize_rgb(c, step=16) for c in samples]
    c = Counter(q)
    pal = [rgb for rgb, _ in c.most_common(k)]
    if not pal:
        pal = [(255, 255, 255)]
    return pal


def border_connected_bg_mask_palette(img_rgb: Image.Image, palette: List[Tuple[int, int, int]], tol: int) -> bytearray:
    w, h = img_rgb.size
    px = img_rgb.load()
    tol2 = tol * tol

    visited = bytearray(w * h)
    is_bg = bytearray(w * h)
    q: deque[Tuple[int, int]] = deque()

    def idx(x: int, y: int) -> int:
        return y * w + x

    def try_push(x: int, y: int) -> None:
        i = idx(x, y)
        if visited[i]:
            return
        visited[i] = 1
        if min_dist2(px[x, y], palette) <= tol2:
            is_bg[i] = 1
            q.append((x, y))

    for x in range(w):
        try_push(x, 0)
        try_push(x, h - 1)
    for y in range(h):
        try_push(0, y)
        try_push(w - 1, y)

    while q:
        x, y = q.popleft()
        if x > 0:
            try_push(x - 1, y)
        if x + 1 < w:
            try_push(x + 1, y)
        if y > 0:
            try_push(x, y - 1)
        if y + 1 < h:
            try_push(x, y + 1)

    return is_bg


def make_foreground_rgba(img: Image.Image, remove_bg: bool, tol: int, soft: int) -> Image.Image:
    rgba = img.convert("RGBA")
    if not remove_bg:
        return rgba

    rgb = rgba.convert("RGB")
    palette = detect_bg_palette(rgb, k=3)

    bg0 = palette[0]
    lum = 0.2126 * bg0[0] + 0.7152 * bg0[1] + 0.0722 * bg0[2]

    base_tol = int(tol)
    if lum < 40:
        base_tol = max(6, base_tol - 12)
    if lum > 220:
        base_tol = min(90, base_tol + 10)

    if len(palette) >= 2 and dist2(palette[0], palette[1]) > (40 * 40):
        base_tol = max(base_tol, int(tol) + 15)

    bg_mask = border_connected_bg_mask_palette(rgb, palette, base_tol)

    w, h = rgba.size
    bg_mask_img = Image.frombytes("L", (w, h), bytes(255 if bg_mask[i] else 0 for i in range(w * h)))
    dil = bg_mask_img.filter(ImageFilter.MaxFilter(3))

    old_a = rgba.getchannel("A")
    new_a = Image.new("L", (w, h), 255)

    px_rgb = rgb.load()
    px_dil = dil.load()
    px_new = new_a.load()

    t0 = base_tol
    t1 = base_tol + max(1, int(soft))
    t0_2 = t0 * t0
    t1_2 = t1 * t1

    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            if bg_mask[i]:
                px_new[x, y] = 0
                continue

            if px_dil[x, y] > 0:
                d = min_dist2(px_rgb[x, y], palette)
                if d <= t0_2:
                    px_new[x, y] = 0
                elif d >= t1_2:
                    px_new[x, y] = 255
                else:
                    dd = (d ** 0.5)
                    a = int(round(255 * (dd - t0) / (t1 - t0)))
                    px_new[x, y] = clamp(a, 0, 255)
            else:
                px_new[x, y] = 255

    final_a = ImageChops.multiply(old_a, new_a)
    out = rgba.copy()
    out.putalpha(final_a)
    return out


def bbox_from_alpha(img_rgba: Image.Image, thresh: int = 4) -> Optional[Tuple[int, int, int, int]]:
    a = img_rgba.getchannel("A")
    a2 = a.point(lambda p: 255 if p >= thresh else 0)
    return a2.getbbox()


def expand_bbox(bb: Tuple[int, int, int, int], pad: int, w: int, h: int) -> Tuple[int, int, int, int]:
    l, t, r, b = bb
    l = clamp(l - pad, 0, w)
    t = clamp(t - pad, 0, h)
    r = clamp(r + pad, 0, w)
    b = clamp(b + pad, 0, h)
    if r <= l:
        r = min(w, l + 1)
    if b <= t:
        b = min(h, t + 1)
    return (l, t, r, b)


def rounded_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    w, h = size
    r = max(0, int(radius))
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    if r <= 0:
        d.rectangle((0, 0, w, h), fill=255)
        return m
    d.rounded_rectangle((0, 0, w, h), radius=r, fill=255)
    return m


def apply_alpha_mask(img_rgba: Image.Image, mask_l: Image.Image) -> Image.Image:
    a = img_rgba.getchannel("A")
    a2 = ImageChops.multiply(a, mask_l)
    out = img_rgba.copy()
    out.putalpha(a2)
    return out


def add_shadow_and_outline(
    layer_rgba: Image.Image,
    shadow_offset: Tuple[int, int],
    shadow_blur: int,
    shadow_alpha: int,
    outline_px: int,
    outline_alpha: int,
) -> Image.Image:
    w, h = layer_rgba.size
    a = layer_rgba.getchannel("A")

    sh_mask = a.filter(ImageFilter.GaussianBlur(max(0, int(shadow_blur))))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_color = Image.new("RGBA", (w, h), (0, 0, 0, clamp(int(shadow_alpha), 0, 255)))
    shadow.paste(shadow_color, (int(shadow_offset[0]), int(shadow_offset[1])), sh_mask)

    if outline_px > 0 and outline_alpha > 0:
        k = outline_px * 2 + 1
        dil = a.filter(ImageFilter.MaxFilter(k))
        edge = ImageChops.subtract(dil, a).filter(ImageFilter.GaussianBlur(0.6))
        outl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        outline_color = Image.new("RGBA", (w, h), (0, 0, 0, clamp(int(outline_alpha), 0, 255)))
        outl.paste(outline_color, (0, 0), edge)
    else:
        outl = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    base.alpha_composite(shadow)
    base.alpha_composite(outl)
    base.alpha_composite(layer_rgba)
    return base


def compose(
    img_rgba: Image.Image,
    target_w: int,
    target_h: int,
    bg_hex: str,
    transparent_bg: bool,
    bg_alpha: int,
    round_radius: int,
    fill_percent: int,
    zoom_percent: int,
    pan_x: int,
    pan_y: int,
    crop_pad: int,
    shadow: bool = True,
    outline: bool = True,
) -> Image.Image:
    tw = max(10, int(target_w))
    th = max(10, int(target_h))

    if transparent_bg:
        canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        bg_mask = None
    else:
        r, g, b = hex_to_rgb(bg_hex)
        a = clamp(int(bg_alpha), 0, 255)
        canvas = Image.new("RGBA", (tw, th), (r, g, b, a))
        bg_mask = rounded_mask((tw, th), int(round_radius)) if int(round_radius) > 0 else None
        if bg_mask is not None:
            canvas = apply_alpha_mask(canvas, bg_mask)

    bb = bbox_from_alpha(img_rgba, thresh=4)
    if bb is None:
        return canvas

    w, h = img_rgba.size
    bb = expand_bbox(bb, int(crop_pad), w, h)
    fg = img_rgba.crop(bb)

    fw, fh = fg.size
    max_side = int(round(min(tw, th) * (clamp(int(fill_percent), 10, 100) / 100.0)))
    max_side = clamp(max_side, 10, min(tw, th))

    base_scale = min(max_side / max(1, fw), max_side / max(1, fh))
    zoom = clamp(int(zoom_percent), 10, 400) / 100.0
    scale = base_scale * zoom

    nw = max(1, int(round(fw * scale)))
    nh = max(1, int(round(fh * scale)))
    fg = fg.resize((nw, nh), Image.LANCZOS)

    x = (tw - nw) // 2 + int(pan_x)
    y = (th - nh) // 2 + int(pan_y)

    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    layer.alpha_composite(fg, dest=(x, y))

    if shadow or outline:
        layer = add_shadow_and_outline(
            layer,
            shadow_offset=(2, 3) if tw <= 110 else (3, 4),
            shadow_blur=3 if tw <= 110 else 4,
            shadow_alpha=80 if shadow else 0,
            outline_px=1 if outline else 0,
            outline_alpha=70 if outline else 0,
        )

    out = canvas.copy()
    out.alpha_composite(layer)

    if bg_mask is not None:
        out = apply_alpha_mask(out, bg_mask)

    return out


def compose_split_2frames(
    img_rgba: Image.Image,
    orientation: str,
    bg_hex: str,
    transparent_bg: bool,
    bg_alpha: int,
    round_radius: int,
    fill_percent: int,
    zoom_percent: int,
    pan_x: int,
    pan_y: int,
    crop_pad: int,
) -> Tuple[Image.Image, Image.Image]:
    if orientation == "h":
        inter_w, inter_h = 200, 100
        combined = compose(
            img_rgba,
            inter_w,
            inter_h,
            bg_hex,
            transparent_bg,
            bg_alpha,
            round_radius,
            fill_percent,
            zoom_percent,
            pan_x,
            pan_y,
            crop_pad,
        )
        a = combined.crop((0, 0, 100, 100))
        b = combined.crop((100, 0, 200, 100))
        return a, b

    inter_w, inter_h = 100, 200
    combined = compose(
        img_rgba,
        inter_w,
        inter_h,
        bg_hex,
        transparent_bg,
        bg_alpha,
        round_radius,
        fill_percent,
        zoom_percent,
        pan_x,
        pan_y,
        crop_pad,
    )
    a = combined.crop((0, 0, 100, 100))
    b = combined.crop((0, 100, 100, 200))
    return a, b


def checkerboard(size: int = 280, cell: int = 14) -> Image.Image:
    a = (220, 220, 220, 255)
    b = (180, 180, 180, 255)
    img = Image.new("RGBA", (size, size), a)
    px = img.load()
    for y in range(size):
        for x in range(size):
            if ((x // cell) + (y // cell)) % 2 == 1:
                px[x, y] = b
    return img


@dataclass
class JobConfig:
    out_dir: Path
    bg_hex: str
    transparent_bg: bool
    bg_alpha: int
    round_radius: int
    remove_bg: bool
    tol: int
    soft: int
    fill_percent: int
    zoom_percent: int
    pan_x: int
    pan_y: int
    crop_pad: int
    overwrite: bool
    target_w: int
    target_h: int
    split_mode: str


def process_one(path: Path, cfg: JobConfig) -> Tuple[bool, str]:
    try:
        img = Image.open(path)
        fg = make_foreground_rgba(img, cfg.remove_bg, cfg.tol, cfg.soft)

        cfg.out_dir.mkdir(parents=True, exist_ok=True)

        if cfg.split_mode == SPLIT_MODES[1]:
            a, b = compose_split_2frames(
                fg,
                "h",
                cfg.bg_hex,
                cfg.transparent_bg,
                cfg.bg_alpha,
                cfg.round_radius,
                cfg.fill_percent,
                cfg.zoom_percent,
                cfg.pan_x,
                cfg.pan_y,
                cfg.crop_pad,
            )
            if cfg.overwrite:
                out_a = cfg.out_dir / f"{path.stem}_1.png"
                out_b = cfg.out_dir / f"{path.stem}_2.png"
            else:
                suf = "transparent" if cfg.transparent_bg else f"{cfg.bg_hex.lstrip('#')}_a{cfg.bg_alpha}"
                out_a = cfg.out_dir / f"{path.stem}_{suf}_1.png"
                out_b = cfg.out_dir / f"{path.stem}_{suf}_2.png"
            a.save(out_a, format="PNG", optimize=True, compress_level=9)
            b.save(out_b, format="PNG", optimize=True, compress_level=9)
            return True, f"{out_a.name}, {out_b.name}"

        if cfg.split_mode == SPLIT_MODES[2]:
            a, b = compose_split_2frames(
                fg,
                "v",
                cfg.bg_hex,
                cfg.transparent_bg,
                cfg.bg_alpha,
                cfg.round_radius,
                cfg.fill_percent,
                cfg.zoom_percent,
                cfg.pan_x,
                cfg.pan_y,
                cfg.crop_pad,
            )
            if cfg.overwrite:
                out_a = cfg.out_dir / f"{path.stem}_1.png"
                out_b = cfg.out_dir / f"{path.stem}_2.png"
            else:
                suf = "transparent" if cfg.transparent_bg else f"{cfg.bg_hex.lstrip('#')}_a{cfg.bg_alpha}"
                out_a = cfg.out_dir / f"{path.stem}_{suf}_1.png"
                out_b = cfg.out_dir / f"{path.stem}_{suf}_2.png"
            a.save(out_a, format="PNG", optimize=True, compress_level=9)
            b.save(out_b, format="PNG", optimize=True, compress_level=9)
            return True, f"{out_a.name}, {out_b.name}"

        out = compose(
            fg,
            cfg.target_w,
            cfg.target_h,
            cfg.bg_hex,
            cfg.transparent_bg,
            cfg.bg_alpha,
            cfg.round_radius,
            cfg.fill_percent,
            cfg.zoom_percent,
            cfg.pan_x,
            cfg.pan_y,
            cfg.crop_pad,
        )

        if cfg.overwrite:
            out_path = cfg.out_dir / path.name
        else:
            suf = "transparent" if cfg.transparent_bg else f"{cfg.bg_hex.lstrip('#')}_a{cfg.bg_alpha}"
            out_path = cfg.out_dir / f"{path.stem}_{suf}.png"

        out.save(out_path, format="PNG", optimize=True, compress_level=9)
        return True, out_path.name
    except Exception as e:
        return False, f"{path.name}: {e}"


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, *, height: int = 200) -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(self, height=height, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._bind_mousewheel(self.canvas)

    def _on_inner_configure(self, _e) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)

    def _bind_mousewheel(self, w: tk.Widget) -> None:
        def _on_mousewheel(event):
            delta = 0
            if hasattr(event, "delta") and event.delta:
                delta = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) in (4, 5):
                delta = -1 if event.num == 4 else 1
            if delta:
                self.canvas.yview_scroll(delta, "units")
            return "break"

        w.bind_all("<MouseWheel>", _on_mousewheel)
        w.bind_all("<Button-4>", _on_mousewheel)
        w.bind_all("<Button-5>", _on_mousewheel)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PNG – фон, очистка, кадрирование, 100x100/200x100, 2 кадра")
        self.geometry("1040x700")
        self.minsize(900, 600)

        self.selected: List[Path] = []
        self.queue: deque = deque()
        self.worker: Optional[threading.Thread] = None

        self.bg_var = tk.StringVar(value=BG_COLORS[0])
        self.transparent_bg_var = tk.BooleanVar(value=False)

        self.bg_alpha_var = tk.IntVar(value=255)
        self.round_radius_var = tk.IntVar(value=0)

        self.out_dir_var = tk.StringVar(value=str(Path.cwd() / "out_icons"))
        self.remove_bg_var = tk.BooleanVar(value=True)
        self.tol_var = tk.IntVar(value=28)
        self.soft_var = tk.IntVar(value=28)

        self.fill_var = tk.IntVar(value=84)
        self.zoom_var = tk.IntVar(value=100)
        self.pan_x_var = tk.IntVar(value=0)
        self.pan_y_var = tk.IntVar(value=0)
        self.crop_pad_var = tk.IntVar(value=6)

        self.overwrite_var = tk.BooleanVar(value=False)

        self.target_w_var = tk.IntVar(value=100)
        self.target_h_var = tk.IntVar(value=100)

        self.split_mode_var = tk.StringVar(value=SPLIT_MODES[0])

        self._preview_job: Optional[str] = None
        self._checker = checkerboard(320, 16)

        self._src_photo: Optional[ImageTk.PhotoImage] = None
        self._out_photo: Optional[ImageTk.PhotoImage] = None

        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsw")
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        top = ttk.Frame(left)
        top.pack(fill="x")

        ttk.Button(top, text="Файлы", command=self.pick_files).pack(side="left")
        ttk.Button(top, text="Папка", command=self.pick_folder).pack(side="left", padx=6)
        ttk.Button(top, text="Очистить", command=self.clear_list).pack(side="left")

        self.count_lbl = ttk.Label(top, text="0")
        self.count_lbl.pack(side="right")

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="x", pady=10)

        self.listbox = tk.Listbox(list_frame, height=12, width=44)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)

        list_frame.columnconfigure(0, weight=1)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.schedule_preview())

        opts_wrap = ttk.LabelFrame(left, text="Параметры", padding=0)
        opts_wrap.pack(fill="both", expand=True)

        opts_scroll = ScrollableFrame(opts_wrap, height=340)
        opts_scroll.pack(fill="both", expand=True, padx=8, pady=8)
        opts = opts_scroll.inner

        def row() -> ttk.Frame:
            r = ttk.Frame(opts)
            r.pack(fill="x", pady=5)
            return r

        r1 = row()
        ttk.Label(r1, text="Фон").pack(side="left")
        self.bg_menu = ttk.OptionMenu(
            r1, self.bg_var, self.bg_var.get(), *BG_COLORS, command=lambda _v: self.schedule_preview()
        )
        self.bg_menu.pack(side="left", padx=8)

        ttk.Checkbutton(
            r1,
            text="Прозрачный фон",
            variable=self.transparent_bg_var,
            command=self._on_transparent_toggle,
        ).pack(side="left", padx=8)

        r1b = row()
        ttk.Label(r1b, text="Прозрачность фона").pack(side="left")
        ttk.Scale(
            r1b,
            from_=0,
            to=255,
            variable=self.bg_alpha_var,
            orient="horizontal",
            length=180,
            command=lambda _v: self.schedule_preview(),
        ).pack(side="left", padx=8)
        self.bg_alpha_lbl = ttk.Label(r1b, text="255")
        self.bg_alpha_lbl.pack(side="left")

        r1c = row()
        ttk.Label(r1c, text="Закругление фона").pack(side="left")
        ttk.Scale(
            r1c,
            from_=0,
            to=30,
            variable=self.round_radius_var,
            orient="horizontal",
            length=180,
            command=lambda _v: self.schedule_preview(),
        ).pack(side="left", padx=8)
        self.round_lbl = ttk.Label(r1c, text="0")
        self.round_lbl.pack(side="left")

        r2 = row()
        ttk.Label(r2, text="Выход").pack(side="left")
        ttk.Entry(r2, textvariable=self.out_dir_var, width=28).pack(side="left", padx=6)
        ttk.Button(r2, text="...", width=3, command=self.pick_out_dir).pack(side="left")

        r3 = row()
        ttk.Checkbutton(
            r3, text="Очистка фона в прозрачный", variable=self.remove_bg_var, command=self.schedule_preview
        ).pack(side="left")

        r4 = row()
        ttk.Label(r4, text="Tol").pack(side="left")
        ttk.Scale(
            r4,
            from_=0,
            to=90,
            variable=self.tol_var,
            orient="horizontal",
            length=170,
            command=lambda _v: self.schedule_preview(),
        ).pack(side="left", padx=8)
        self.tol_lbl = ttk.Label(r4, text="28")
        self.tol_lbl.pack(side="left")

        r5 = row()
        ttk.Label(r5, text="Soft").pack(side="left")
        ttk.Scale(
            r5,
            from_=0,
            to=60,
            variable=self.soft_var,
            orient="horizontal",
            length=170,
            command=lambda _v: self.schedule_preview(),
        ).pack(side="left", padx=8)
        self.soft_lbl = ttk.Label(r5, text="28")
        self.soft_lbl.pack(side="left")

        ttk.Separator(opts).pack(fill="x", pady=8)

        r6 = row()
        ttk.Label(r6, text="Размер (W×H)").pack(side="left")
        ttk.Spinbox(r6, from_=50, to=400, textvariable=self.target_w_var, width=6, command=self.schedule_preview).pack(
            side="left", padx=(8, 4)
        )
        ttk.Spinbox(r6, from_=50, to=400, textvariable=self.target_h_var, width=6, command=self.schedule_preview).pack(
            side="left", padx=4
        )
        ttk.Button(r6, text="100×100", width=8, command=lambda: self._set_target(100, 100)).pack(side="left", padx=6)
        ttk.Button(r6, text="200×100", width=8, command=lambda: self._set_target(200, 100)).pack(side="left")

        r7 = row()
        ttk.Label(r7, text="Раскадровка").pack(side="left")
        ttk.OptionMenu(
            r7,
            self.split_mode_var,
            self.split_mode_var.get(),
            *SPLIT_MODES,
            command=lambda _v: self.schedule_preview(),
        ).pack(side="left", padx=8)

        def slider_row(label: str, var: tk.IntVar, lo: int, hi: int, fmt) -> None:
            r = row()
            ttk.Label(r, text=label).pack(side="left")
            ttk.Scale(
                r,
                from_=lo,
                to=hi,
                variable=var,
                orient="horizontal",
                length=170,
                command=lambda _v: self.schedule_preview(),
            ).pack(side="left", padx=8)
            lbl = ttk.Label(r, text=fmt(var.get()))
            lbl.pack(side="left")

            def upd(*_):
                lbl.config(text=fmt(int(var.get())))
                self.tol_lbl.config(text=str(int(self.tol_var.get())))
                self.soft_lbl.config(text=str(int(self.soft_var.get())))
                self.bg_alpha_lbl.config(text=str(int(self.bg_alpha_var.get())))
                self.round_lbl.config(text=str(int(self.round_radius_var.get())))

            var.trace_add("write", upd)
            upd()

        slider_row("Заполнение", self.fill_var, 60, 98, lambda v: f"{v}%")
        slider_row("Зум", self.zoom_var, 70, 170, lambda v: f"{v}%")
        slider_row("Сдвиг X", self.pan_x_var, -60, 60, lambda v: str(v))
        slider_row("Сдвиг Y", self.pan_y_var, -60, 60, lambda v: str(v))
        slider_row("Кадрирование", self.crop_pad_var, -30, 40, lambda v: f"{v}px")

        r_last = row()
        ttk.Checkbutton(r_last, text="Перезапись имени", variable=self.overwrite_var).pack(side="left")

        bottom = ttk.Frame(left)
        bottom.pack(fill="x", pady=10)
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x")
        self.run_btn = ttk.Button(btn_row, text="Обработать", command=self.run)
        self.run_btn.pack(side="left")
        self.status_lbl = ttk.Label(btn_row, text="")
        self.status_lbl.pack(side="left", padx=10)

        prev = ttk.LabelFrame(right, text="Предпросмотр", padding=10)
        prev.grid(row=0, column=0, sticky="nsew")
        prev.rowconfigure(0, weight=1)
        prev.columnconfigure(0, weight=1)

        grid = ttk.Frame(prev)
        grid.grid(row=0, column=0, sticky="nsew")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)

        self.src_canvas = tk.Canvas(grid, width=320, height=320, bg="#222222", highlightthickness=0)
        self.src_canvas.grid(row=0, column=0, padx=10, pady=10, sticky="n")

        self.out_canvas = tk.Canvas(grid, width=320, height=320, bg="#222222", highlightthickness=0)
        self.out_canvas.grid(row=0, column=1, padx=10, pady=10, sticky="n")

        info = ttk.Frame(prev)
        info.grid(row=1, column=0, sticky="ew")
        ttk.Label(info, text="Слева – объект с альфой, справа – итог или 2 кадра").pack(side="left")

        self._on_transparent_toggle()

    def _set_target(self, w: int, h: int) -> None:
        self.target_w_var.set(int(w))
        self.target_h_var.set(int(h))
        self.schedule_preview()

    def _on_transparent_toggle(self) -> None:
        state = "disabled" if self.transparent_bg_var.get() else "normal"
        try:
            self.bg_menu.configure(state=state)
        except Exception:
            LOG.debug("bg_menu state update failed", exc_info=True)
        self.schedule_preview()

    def pick_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Выбрать PNG", filetypes=[("PNG", "*.png")])
        if not paths:
            return
        for p in paths:
            self._add_file(Path(p))
        self._refresh_list()

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="Папка с PNG")
        if not d:
            return
        for p in sorted(Path(d).glob("*.png")):
            self._add_file(p)
        self._refresh_list()

    def pick_out_dir(self) -> None:
        d = filedialog.askdirectory(title="Выходная папка")
        if not d:
            return
        self.out_dir_var.set(d)

    def clear_list(self) -> None:
        self.selected = []
        self._refresh_list()
        self.clear_preview()

    def _add_file(self, p: Path) -> None:
        p = p.resolve()
        if p not in self.selected:
            self.selected.append(p)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for p in self.selected:
            self.listbox.insert("end", str(p))
        self.count_lbl.config(text=f"{len(self.selected)}")

    def get_selected_path(self) -> Optional[Path]:
        sel = self.listbox.curselection()
        if not sel:
            return None
        i = int(sel[0])
        if i < 0 or i >= len(self.selected):
            return None
        return self.selected[i]

    def clear_preview(self) -> None:
        self.src_canvas.delete("all")
        self.out_canvas.delete("all")

    def schedule_preview(self) -> None:
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except Exception:
                LOG.debug("after_cancel failed", exc_info=True)
        self._preview_job = self.after(140, self.update_preview)

    def update_preview(self) -> None:
        self._preview_job = None
        p = self.get_selected_path()
        if p is None or not p.exists():
            self.clear_preview()
            return

        try:
            img = Image.open(p)
            fg = make_foreground_rgba(
                img,
                bool(self.remove_bg_var.get()),
                int(self.tol_var.get()),
                int(self.soft_var.get()),
            )

            src_prev = fg.copy()
            src_prev.thumbnail((320, 320), Image.LANCZOS)
            cb = self._checker.copy().crop((0, 0, src_prev.size[0], src_prev.size[1]))
            cb.alpha_composite(src_prev, dest=(0, 0))
            self._src_photo = ImageTk.PhotoImage(cb)

            split_mode = self.split_mode_var.get()
            if split_mode == SPLIT_MODES[1]:
                a, b = compose_split_2frames(
                    fg,
                    "h",
                    self.bg_var.get(),
                    bool(self.transparent_bg_var.get()),
                    int(self.bg_alpha_var.get()),
                    int(self.round_radius_var.get()),
                    int(self.fill_var.get()),
                    int(self.zoom_var.get()),
                    int(self.pan_x_var.get()),
                    int(self.pan_y_var.get()),
                    int(self.crop_pad_var.get()),
                )
                preview = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
                cb2 = self._checker.copy()
                cb2 = cb2.crop((0, 0, 320, 320))

                a_big = a.resize((150, 150), Image.NEAREST)
                b_big = b.resize((150, 150), Image.NEAREST)
                cb2.alpha_composite(a_big, dest=(10, 85))
                cb2.alpha_composite(b_big, dest=(160, 85))
                self._out_photo = ImageTk.PhotoImage(cb2)

            elif split_mode == SPLIT_MODES[2]:
                a, b = compose_split_2frames(
                    fg,
                    "v",
                    self.bg_var.get(),
                    bool(self.transparent_bg_var.get()),
                    int(self.bg_alpha_var.get()),
                    int(self.round_radius_var.get()),
                    int(self.fill_var.get()),
                    int(self.zoom_var.get()),
                    int(self.pan_x_var.get()),
                    int(self.pan_y_var.get()),
                    int(self.crop_pad_var.get()),
                )
                cb2 = self._checker.copy().crop((0, 0, 320, 320))
                a_big = a.resize((150, 150), Image.NEAREST)
                b_big = b.resize((150, 150), Image.NEAREST)
                cb2.alpha_composite(a_big, dest=(85, 10))
                cb2.alpha_composite(b_big, dest=(85, 160))
                self._out_photo = ImageTk.PhotoImage(cb2)

            else:
                out = compose(
                    fg,
                    int(self.target_w_var.get()),
                    int(self.target_h_var.get()),
                    self.bg_var.get(),
                    bool(self.transparent_bg_var.get()),
                    int(self.bg_alpha_var.get()),
                    int(self.round_radius_var.get()),
                    int(self.fill_var.get()),
                    int(self.zoom_var.get()),
                    int(self.pan_x_var.get()),
                    int(self.pan_y_var.get()),
                    int(self.crop_pad_var.get()),
                )

                out_prev = out.copy()
                out_prev = out_prev.resize((320, 320), Image.NEAREST)
                if bool(self.transparent_bg_var.get()) or int(self.bg_alpha_var.get()) < 255 or int(self.round_radius_var.get()) > 0:
                    cb2 = self._checker.copy().crop((0, 0, 320, 320))
                    cb2.alpha_composite(out_prev, dest=(0, 0))
                    self._out_photo = ImageTk.PhotoImage(cb2)
                else:
                    self._out_photo = ImageTk.PhotoImage(out_prev)

            self.src_canvas.delete("all")
            self.src_canvas.create_image(0, 0, anchor="nw", image=self._src_photo)
            self.out_canvas.delete("all")
            self.out_canvas.create_image(0, 0, anchor="nw", image=self._out_photo)

        except Exception:
            self.clear_preview()

    def run(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.selected:
            messagebox.showinfo("Нет файлов", "Выбери PNG файлы или папку")
            return

        out_dir = Path(self.out_dir_var.get()).expanduser().resolve()

        cfg = JobConfig(
            out_dir=out_dir,
            bg_hex=self.bg_var.get(),
            transparent_bg=bool(self.transparent_bg_var.get()),
            bg_alpha=int(self.bg_alpha_var.get()),
            round_radius=int(self.round_radius_var.get()),
            remove_bg=bool(self.remove_bg_var.get()),
            tol=int(self.tol_var.get()),
            soft=int(self.soft_var.get()),
            fill_percent=int(self.fill_var.get()),
            zoom_percent=int(self.zoom_var.get()),
            pan_x=int(self.pan_x_var.get()),
            pan_y=int(self.pan_y_var.get()),
            crop_pad=int(self.crop_pad_var.get()),
            overwrite=bool(self.overwrite_var.get()),
            target_w=int(self.target_w_var.get()),
            target_h=int(self.target_h_var.get()),
            split_mode=self.split_mode_var.get(),
        )

        self.run_btn.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.selected)
        self.status_lbl.config(text="")

        def worker() -> None:
            ok = 0
            for i, p in enumerate(self.selected, start=1):
                success, msg = process_one(p, cfg)
                if success:
                    ok += 1
                self.queue.append(("step", i, ok, msg, success))
            self.queue.append(("done", ok, len(self.selected)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _poll_queue(self) -> None:
        while self.queue:
            item = self.queue.popleft()
            if item[0] == "step":
                _, i, ok, _msg, _success = item
                self.progress["value"] = i
                self.status_lbl.config(text=f"{ok}/{len(self.selected)}")
            elif item[0] == "done":
                _, ok, total = item
                self.run_btn.config(state="normal")
                self.status_lbl.config(text=f"Готово: {ok}/{total}")
        self.after(120, self._poll_queue)


if __name__ == "__main__":
    App().mainloop()
