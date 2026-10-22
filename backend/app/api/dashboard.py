from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import SessionDep
from app.config import get_settings
from app.crawler.errors import CATEGORIES
from app.models import CrawlRun, Lot, PriceSnapshot, Source
from app.models.entities import utcnow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(session: SessionDep, days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    now = utcnow()
    since = now - timedelta(days=days)
    day_ago = now - timedelta(days=1)
    tz = ZoneInfo(get_settings().timezone)

    ok = func.sum(case((CrawlRun.status == "success", 1), else_=0))
    failed = func.sum(case((CrawlRun.status == "failed", 1), else_=0))

    totals = {
        "sources": await session.scalar(select(func.count()).select_from(Source)) or 0,
        "sources_enabled": await session.scalar(select(func.count()).select_from(Source).where(Source.enabled)) or 0,
        "lots": await session.scalar(select(func.count()).select_from(Lot)) or 0,
        "live_lots": await session.scalar(select(func.count()).select_from(Lot).where(Lot.status == "live")) or 0,
        "new_lots_24h": await session.scalar(select(func.count()).select_from(Lot).where(Lot.first_seen_at >= day_ago)) or 0,
        "price_changes_24h": await session.scalar(
            select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.captured_at >= day_ago)
        ) or 0,
    }
    row = (await session.execute(
        select(func.count(), ok, failed, func.avg(CrawlRun.duration_ms)).where(CrawlRun.created_at >= day_ago)
    )).one()
    totals.update(runs_24h=row[0] or 0, success_24h=row[1] or 0, failed_24h=row[2] or 0,
                  avg_duration_ms_24h=int(row[3] or 0))
    totals["success_rate_24h"] = round(totals["success_24h"] / totals["runs_24h"], 4) if totals["runs_24h"] else None

    # 每日趋势（按配置时区分日）
    runs = (await session.execute(
        select(CrawlRun.created_at, CrawlRun.status, CrawlRun.items_new).where(CrawlRun.created_at >= since)
    )).all()
    daily: dict[str, dict[str, int]] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).astimezone(tz).strftime("%Y-%m-%d")
        daily[d] = {"success": 0, "failed": 0, "new_lots": 0}
    for created, status, new in runs:
        d = created.astimezone(tz).strftime("%Y-%m-%d")
        if d in daily:
            if status in ("success", "failed"):
                daily[d][status] += 1
            daily[d]["new_lots"] += new or 0

    reasons = (await session.execute(
        select(CrawlRun.error_type, func.count()).where(CrawlRun.created_at >= since, CrawlRun.status == "failed")
        .group_by(CrawlRun.error_type)
    )).all()

    per_source = (await session.execute(
        select(
            Source.id, Source.name, Source.health, Source.login_expired_at, Source.enabled,
            func.count(CrawlRun.id), ok, failed, func.avg(CrawlRun.duration_ms), func.max(CrawlRun.created_at),
        )
        .outerjoin(CrawlRun, (CrawlRun.source_id == Source.id) & (CrawlRun.created_at >= since))
        .group_by(Source.id).order_by(Source.id)
    )).all()
    lot_counts = dict((await session.execute(select(Lot.source_id, func.count()).group_by(Lot.source_id))).all())

    recent_failures = (await session.scalars(
        select(CrawlRun).where(CrawlRun.status == "failed").order_by(CrawlRun.id.desc()).limit(10)
    )).all()
    names = {s[0]: s[1] for s in per_source}

    return {
        "totals": totals,
        "daily": [{"date": d, **v} for d, v in daily.items()],
        "failure_reasons": [
            {"type": t or "other", "label": CATEGORIES.get(t or "other", t), "count": n} for t, n in reasons
        ],
        "sources": [
            {
                "id": sid, "name": name, "health": health, "login_expired": expired is not None, "enabled": enabled,
                "runs": total or 0, "success": succ or 0, "failed": fail or 0,
                "success_rate": round((succ or 0) / total, 4) if total else None,
                "avg_duration_ms": int(avg or 0), "last_run_at": last, "lots": lot_counts.get(sid, 0),
            }
            for sid, name, health, expired, enabled, total, succ, fail, avg, last in per_source
        ],
        "recent_failures": [
            {"id": r.id, "source_id": r.source_id, "source_name": names.get(r.source_id), "error": (r.error or "")[:300],
             "error_type": r.error_type, "label": CATEGORIES.get(r.error_type or "other"), "created_at": r.created_at}
            for r in recent_failures
        ],
    }
