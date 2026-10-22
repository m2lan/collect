import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.transforms import Price, parse_status
from app.models import Lot, PriceSnapshot
from app.models.entities import utcnow

STANDARD_FIELDS = {
    "title", "url", "image", "current_price", "start_price", "deal_price",
    "currency", "bid_count", "status", "start_time", "end_time",
}
PRICE_FIELDS = ("current_price", "start_price", "deal_price")
# 常见的字段别名，方便规则里直接用 price 等短名
ALIASES = {"price": "current_price", "bids": "bid_count", "img": "image", "link": "url", "name": "title"}


@dataclass
class UpsertStats:
    new: int = 0
    updated: int = 0
    new_ids: list[int] = field(default_factory=list)
    # 价格变化的拍品：(lot_id, 旧价格, 新价格)
    price_changes: list[tuple[int, Decimal | None, Decimal | None]] = field(default_factory=list)


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def normalize_item(raw: dict[str, Any], unique_field: str | None) -> dict[str, Any]:
    """把抽取结果映射为 Lot 的标准列，其余字段放入 extra。"""
    data: dict[str, Any] = {"extra": {}}
    for key, value in raw.items():
        target = ALIASES.get(key, key)
        if target in STANDARD_FIELDS and target not in data:
            data[target] = value
        else:
            data["extra"][key] = jsonable(value)

    for key in PRICE_FIELDS:
        value = data.get(key)
        if isinstance(value, Price) and value.currency and not data.get("currency"):
            data["currency"] = value.currency
        if value is not None and not isinstance(value, Decimal):
            data[key] = None
            data["extra"][f"{key}_raw"] = jsonable(value)
    for key in ("start_time", "end_time"):
        if data.get(key) is not None and not isinstance(data[key], datetime):
            data["extra"][f"{key}_raw"] = jsonable(data.pop(key))
    if data.get("bid_count") is not None and not isinstance(data["bid_count"], int):
        try:
            data["bid_count"] = int(str(data["bid_count"]).strip())
        except ValueError:
            data["extra"]["bid_count_raw"] = data.pop("bid_count")
    if isinstance(data.get("status"), str) and data["status"] not in {"upcoming", "live", "ended", "unsold"}:
        data["extra"]["status_raw"] = data["status"]
        data["status"] = parse_status(data["status"])
    for key in ("title", "url", "image", "currency"):
        if data.get(key) is not None:
            data[key] = str(data[key])

    key_value = raw.get(unique_field) if unique_field else None
    if key_value is None:
        key_value = data.get("url")
    if key_value is None:
        key_value = hashlib.sha1(
            json.dumps({k: jsonable(v) for k, v in raw.items()}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    data["unique_key"] = str(key_value)[:512]
    return data


async def upsert_lots(
    session: AsyncSession, source_id: int, run_id: int | None, items: list[dict[str, Any]], unique_field: str | None
) -> UpsertStats:
    stats = UpsertStats()
    normalized: dict[str, dict[str, Any]] = {}
    for raw in items:
        data = normalize_item(raw, unique_field)
        normalized[data["unique_key"]] = data
    if not normalized:
        return stats

    existing = {
        lot.unique_key: lot
        for lot in (
            await session.scalars(
                select(Lot).where(Lot.source_id == source_id, Lot.unique_key.in_(list(normalized)))
            )
        ).all()
    }
    now = utcnow()
    for key, data in normalized.items():
        lot = existing.get(key)
        price = data.get("deal_price") or data.get("current_price")
        if lot is None:
            lot = Lot(source_id=source_id, first_seen_at=now, last_seen_at=now, **data)
            session.add(lot)
            await session.flush()
            stats.new += 1
            stats.new_ids.append(lot.id)
            changed = True
        else:
            old = (lot.deal_price or lot.current_price, lot.bid_count, lot.status)
            for field, value in data.items():
                if field == "extra":
                    lot.extra = {**(lot.extra or {}), **value}
                elif value is not None:
                    setattr(lot, field, value)
            lot.last_seen_at = now
            new = (lot.deal_price or lot.current_price, lot.bid_count, lot.status)
            changed = _changed(old, new)
            if changed:
                stats.updated += 1
                if _changed((old[0],), (new[0],)):
                    stats.price_changes.append((lot.id, old[0], new[0]))
        if changed:
            session.add(
                PriceSnapshot(
                    lot_id=lot.id, crawl_run_id=run_id, price=price if price is not None else lot.current_price,
                    bid_count=lot.bid_count, status=lot.status, captured_at=now,
                )
            )
    return stats


def _changed(old: tuple, new: tuple) -> bool:
    def norm(v: Any) -> Any:
        return Decimal(v).quantize(Decimal("0.01")) if isinstance(v, Decimal) else v

    return tuple(map(norm, old)) != tuple(map(norm, new))
