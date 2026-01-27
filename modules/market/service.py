from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_dt(v: Any) -> str:
    if isinstance(v, datetime):
        try:
            return v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return v.strftime("%Y-%m-%d %H:%M")
    s = str(v or "")
    if "T" in s:
        s = s.replace("T", " ")
    return s[:16]


@dataclass(frozen=True)
class MarketListingView:
    id: int
    item_name: str
    qty: int
    price: int
    seller_tg_id: int
    created_at: str


class MarketService:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_active(self, limit: int = 30) -> List[MarketListingView]:
        rows = (
            await self._s.execute(
                text(
                    """
                    SELECT
                      ml.id,
                      ml.qty,
                      ml.price,
                      ml.created_at,
                      i.name AS item_name,
                      u.tg_id AS seller_tg_id
                    FROM market_listings ml
                    JOIN items i ON i.id = ml.item_id
                    JOIN users u ON u.id = ml.seller_user_id
                    WHERE ml.status = 'active'
                    ORDER BY ml.created_at DESC, ml.id DESC
                    LIMIT :lim
                    """
                ),
                {"lim": int(limit)},
            )
        ).mappings().all()

        out: list[MarketListingView] = []
        for r in rows:
            out.append(
                MarketListingView(
                    id=int(r.get("id") or 0),
                    item_name=str(r.get("item_name") or "Предмет"),
                    qty=int(r.get("qty") or 1),
                    price=int(r.get("price") or 0),
                    seller_tg_id=int(r.get("seller_tg_id") or 0),
                    created_at=_fmt_dt(r.get("created_at")),
                )
            )
        return out

    async def market_text(self, limit: int = 30) -> str:
        listings = await self.list_active(limit=limit)
        if not listings:
            return "<b>Рынок</b>\nЛотов нет."

        lines: list[str] = [
            "<b>Рынок</b>",
            f"Активные лоты: {len(listings)}",
            "",
        ]

        for l in listings:
            name = _esc(l.item_name)
            qty = max(1, int(l.qty))
            price = max(0, int(l.price))
            seller = int(l.seller_tg_id)
            dt = _esc(l.created_at)

            qty_part = f" ×{qty}" if qty > 1 else ""
            seller_part = f"продавец {seller}" if seller else "продавец неизвестен"
            lines.append(f"#{l.id} – {name}{qty_part} – {price} монет – {seller_part} – {dt}")

        return "\n".join(lines).rstrip()
