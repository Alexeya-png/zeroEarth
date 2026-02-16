from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore


API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Defaults (can be overridden by CLI args)
DEFAULT_TIMEOUT_S = 300
MAX_RETRIES = 6
MAX_STICKER_BYTES = 512 * 1024  # 512 KB typical limit for stickers/custom emoji

_SESSION = requests.Session()


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _sanitize_key(stem: str) -> str:
    s = stem.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z_\-]", "", s)
    s = s.strip("_")
    return s or "item"


def _fit_to_square_100(src_path: Path, dst_path: Path) -> None:
    if Image is None:
        _die("Pillow is required: pip install pillow")

    im = Image.open(src_path).convert("RGBA")
    w, h = im.size
    if w <= 0 or h <= 0:
        _die(f"Bad image size: {src_path}")

    target = 100
    scale = min(target / w, target / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    im = im.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    x = (target - new_w) // 2
    y = (target - new_h) // 2
    canvas.paste(im, (x, y), im)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst_path, format="PNG", optimize=True, compress_level=9)

    try:
        size = dst_path.stat().st_size
        if size > MAX_STICKER_BYTES:
            _die(f"Prepared sticker too large: {dst_path.name} ({size} bytes) > {MAX_STICKER_BYTES}")
    except FileNotFoundError:
        _die(f"Failed to write image: {dst_path}")


def tg_call(token: str, method: str, data: Dict[str, Any], files: Optional[Dict[str, Any]] = None) -> Any:
    url = API_BASE.format(token=token, method=method)

    for attempt in range(MAX_RETRIES):
        try:
            if files:
                resp = _SESSION.post(url, data=data, files=files, timeout=(15, DEFAULT_TIMEOUT_S))
            else:
                resp = _SESSION.post(url, json=data, timeout=(15, DEFAULT_TIMEOUT_S))
        except requests.Timeout:
            if attempt == MAX_RETRIES - 1:
                _die(f"Telegram timeout for {method} after {DEFAULT_TIMEOUT_S}s")
            time.sleep(1.5 * (attempt + 1))
            continue
        except requests.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                _die(f"Telegram request failed: {e}")
            time.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code in (502, 503, 504):
            if attempt == MAX_RETRIES - 1:
                _die(f"Telegram gateway error {resp.status_code} for {method}")
            time.sleep(1.5 * (attempt + 1))
            continue

        try:
            payload = resp.json()
        except Exception:
            _die(f"Telegram returned non-JSON for {method}: HTTP {resp.status_code}")

        if payload.get("ok"):
            return payload.get("result")

        err_code = payload.get("error_code")
        params = payload.get("parameters") or {}
        retry_after = params.get("retry_after")

        if err_code == 429 and retry_after:
            time.sleep(float(retry_after) + 0.2)
            continue

        if attempt < MAX_RETRIES - 1 and err_code in (500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue

        _die(f"Telegram error for {method}: {payload.get('description')} (code={err_code})")

    _die(f"Telegram error for {method}: retries exceeded")
    return None


def tg_get_me(token: str) -> Dict[str, Any]:
    return tg_call(token, "getMe", {})


def tg_get_sticker_set(token: str, name: str) -> Dict[str, Any]:
    return tg_call(token, "getStickerSet", {"name": name})


def ensure_pack_name(raw_name: str, bot_username: str) -> str:
    base = raw_name.strip()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^0-9A-Za-z_]", "", base)
    if not base:
        base = "custom_emoji"

    suffix = f"_by_{bot_username.lower()}"
    if not base.lower().endswith(suffix):
        base = re.sub(r"_by_[0-9a-zA-Z_]+$", "", base)
        base = f"{base}{suffix}"

    if len(base) > 64:
        keep = 64 - len(suffix)
        base = f"{base[:keep]}{suffix}"
    return base


@dataclass(frozen=True)
class PreparedSticker:
    key: str
    src_path: Path
    prepared_path: Path
    attach_name: str


def collect_pngs(input_dir: Path) -> List[Path]:
    if not input_dir.exists() or not input_dir.is_dir():
        _die(f"Input dir not found: {input_dir}")
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"])
    if not files:
        _die(f"No .png files found in: {input_dir}")
    return files


def prepare_images(files: List[Path], tmp_dir: Path) -> List[PreparedSticker]:
    prepared: List[PreparedSticker] = []
    used_keys: Dict[str, int] = {}

    for idx, src in enumerate(files, start=1):
        key = _sanitize_key(src.stem)
        if key in used_keys:
            used_keys[key] += 1
            key = f"{key}_{used_keys[key]}"
        else:
            used_keys[key] = 1

        prepared_path = tmp_dir / f"{idx:03d}_{key}.png"
        _fit_to_square_100(src, prepared_path)

        attach_name = f"st{idx:03d}"
        prepared.append(PreparedSticker(key=key, src_path=src, prepared_path=prepared_path, attach_name=attach_name))

    return prepared


def build_input_sticker(ps: PreparedSticker, emoji_list: List[str], keywords_from_key: bool) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "sticker": f"attach://{ps.attach_name}",
        "format": "static",
        "emoji_list": emoji_list,
    }
    if keywords_from_key:
        kw = ps.key[:64]
        if kw:
            obj["keywords"] = [kw]
    return obj


def create_set_with_first_batch(
    token: str,
    owner_user_id: int,
    set_name: str,
    set_title: str,
    batch: List[PreparedSticker],
    emoji_list: List[str],
    keywords_from_key: bool,
) -> None:
    stickers = [build_input_sticker(ps, emoji_list=emoji_list, keywords_from_key=keywords_from_key) for ps in batch]
    data = {
        "user_id": owner_user_id,
        "name": set_name,
        "title": set_title,
        "stickers": json.dumps(stickers, ensure_ascii=False),
        "sticker_type": "custom_emoji",
    }

    files = {ps.attach_name: (ps.prepared_path.name, open(ps.prepared_path, "rb"), "image/png") for ps in batch}
    try:
        tg_call(token, "createNewStickerSet", data=data, files=files)
    finally:
        for _, f, _ in files.values():
            try:
                f.close()
            except Exception:
                logging.getLogger(__name__).debug("file close failed", exc_info=True)


def add_one_sticker(
    token: str,
    owner_user_id: int,
    set_name: str,
    ps: PreparedSticker,
    emoji_list: List[str],
    keywords_from_key: bool,
) -> None:
    sticker_obj = build_input_sticker(ps, emoji_list=emoji_list, keywords_from_key=keywords_from_key)
    data = {
        "user_id": owner_user_id,
        "name": set_name,
        "sticker": json.dumps(sticker_obj, ensure_ascii=False),
    }

    files = {ps.attach_name: (ps.prepared_path.name, open(ps.prepared_path, "rb"), "image/png")}
    try:
        tg_call(token, "addStickerToSet", data=data, files=files)
    finally:
        for _, f, _ in files.values():
            try:
                f.close()
            except Exception:
                logging.getLogger(__name__).debug("file close failed", exc_info=True)


def export_mapping(
    out_path: Path,
    set_name: str,
    set_title: str,
    prepared: List[PreparedSticker],
    sticker_set: Dict[str, Any],
    default_emoji_list: List[str],
) -> None:
    stickers = sticker_set.get("stickers") or []
    if len(stickers) < len(prepared):
        _die(f"Unexpected sticker count in set: {len(stickers)} (expected >= {len(prepared)})")

    items: Dict[str, Any] = {}
    for ps, st in zip(prepared, stickers):
        items[ps.key] = {
            "custom_emoji_id": st.get("custom_emoji_id"),
            "sticker_file_id": st.get("file_id"),
            "file_unique_id": st.get("file_unique_id"),
            "emoji": st.get("emoji") or (default_emoji_list[0] if default_emoji_list else None),
        }

    payload = {
        "kind": "custom_emoji",
        "pack_name": set_name,
        "pack_title": set_title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import PNGs as Telegram custom emoji pack")
    p.add_argument("--input-dir", required=True, help="Directory with source .png files")
    p.add_argument("--owner-user-id", required=True, type=int, help="Telegram user id (owner of the set)")
    p.add_argument("--pack-name", required=True, help="Short name (will be normalized and suffixed with _by_<bot>)")
    p.add_argument("--pack-title", required=True, help="Set title (1-64 chars)")
    p.add_argument("--emoji", default="⭐", help="Fallback emoji for all items (1 emoji)")
    p.add_argument(
        "--keywords-from-filename",
        action="store_true",
        help="Use filename stem as sticker keyword (1 keyword, truncated to 64 chars)",
    )
    p.add_argument("--out-map", default="data/stickers/custom_emoji_map.json", help="Where to write mapping JSON")
    p.add_argument("--tmp-dir", default="out/custom_emoji_prepared", help="Where to write prepared 100x100 PNGs")
    p.add_argument("--token", default=None, help="Second bot token (overrides env)")
    p.add_argument("--env-file", default=".env.emoji", help="Env file for second bot (default: .env.emoji)")
    p.add_argument("--first-batch-size", type=int, default=10, help="How many files to send in createNewStickerSet (1..50)")
    p.add_argument("--timeout-s", type=int, default=300, help="HTTP read timeout seconds")
    p.add_argument("--max-retries", type=int, default=6, help="Max retries for timeouts/429/5xx")
    p.add_argument("--max-sticker-bytes", type=int, default=512 * 1024, help="Max allowed size for prepared sticker file")
    return p.parse_args()


def main() -> None:
    global DEFAULT_TIMEOUT_S, MAX_RETRIES, MAX_STICKER_BYTES

    args = parse_args()

    DEFAULT_TIMEOUT_S = int(args.timeout_s)
    MAX_RETRIES = int(args.max_retries)
    MAX_STICKER_BYTES = int(args.max_sticker_bytes)

    if args.env_file:
        load_dotenv(args.env_file, override=True)
    load_dotenv(override=False)

    token = args.token or os.getenv("EMOJI_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        _die("Token is missing. Use --token or set EMOJI_BOT_TOKEN in .env.emoji")

    me = tg_get_me(token)
    bot_username = me.get("username")
    if not bot_username:
        _die("getMe did not return bot username")

    set_name = ensure_pack_name(args.pack_name, bot_username)
    set_title = str(args.pack_title)[:64]

    input_dir = Path(args.input_dir)
    tmp_dir = Path(args.tmp_dir)
    out_map = Path(args.out_map)

    src_files = collect_pngs(input_dir)
    prepared = prepare_images(src_files, tmp_dir)

    emoji_list = [str(args.emoji)]
    owner_user_id = int(args.owner_user_id)

    first_n = max(1, min(50, int(args.first_batch_size), len(prepared)))
    first_batch = prepared[:first_n]
    rest = prepared[first_n:]

    print(f"Prepared: {len(prepared)} files")
    print(f"Creating set: {set_name}")
    print(f"First batch: {len(first_batch)}")

    create_set_with_first_batch(
        token=token,
        owner_user_id=owner_user_id,
        set_name=set_name,
        set_title=set_title,
        batch=first_batch,
        emoji_list=emoji_list,
        keywords_from_key=bool(args.keywords_from_filename),
    )

    for i, ps in enumerate(rest, start=1):
        add_one_sticker(
            token=token,
            owner_user_id=owner_user_id,
            set_name=set_name,
            ps=ps,
            emoji_list=emoji_list,
            keywords_from_key=bool(args.keywords_from_filename),
        )
        if i % 10 == 0 or i == len(rest):
            print(f"Added: {i}/{len(rest)}")

    sticker_set = tg_get_sticker_set(token, set_name)
    export_mapping(
        out_path=out_map,
        set_name=set_name,
        set_title=set_title,
        prepared=prepared,
        sticker_set=sticker_set,
        default_emoji_list=emoji_list,
    )

    print(f"OK: created {set_name} ({len(prepared)} items)")
    print(f"Map: {out_map}")
    print(f"Prepared PNGs: {tmp_dir}")


if __name__ == "__main__":
    main()
