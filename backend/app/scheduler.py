"""定时采集调度器。

Redis 模式下作为独立进程运行（`python -m app.scheduler`）；未配置 Redis 时随 API 进程启动。
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import CrawlRun, Lot, Rule, Schedule, Source
from app.models.entities import utcnow
from app.settings_store import get_setting
from app.tasks import enqueue_crawl

log = logging.getLogger(__name__)


def validate_cron(expr: str) -> None:
    if len(expr.split()) != 5 or not croniter.is_valid(expr):
        raise ValueError(f"无效的 cron 表达式：{expr}")


def next_fire(expr: str, after: datetime | None = None) -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    base = (after or utcnow()).astimezone(tz)
    return croniter(expr, base).get_next(datetime).astimezone(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite 读回来是 naive UTC


async def tick() -> int:
    """触发所有到期的定时任务，返回创建的采集任务数。"""
    now = utcnow()
    created: list[int] = []
    async with SessionLocal() as session:
        schedules = (await session.scalars(select(Schedule).where(Schedule.enabled, Schedule.next_run_at.is_not(None)))).all()
        for sch in schedules:
            if _aware(sch.next_run_at) > now:  # type: ignore[arg-type]
                continue
            sch.next_run_at = next_fire(sch.cron, now)
            source = await session.get(Source, sch.source_id)
            if source is None or not source.enabled:
                continue
            if not await session.scalar(select(Rule.id).where(Rule.source_id == source.id, Rule.is_active)):
                continue
            busy = await session.scalar(
                select(CrawlRun.id).where(CrawlRun.source_id == source.id, CrawlRun.status.in_(["pending", "running"]))
            )
            if busy:
                log.info("schedule %s skipped: source %s busy", sch.id, source.id)
                continue
            run = CrawlRun(source_id=source.id, trigger="cron", kind=sch.kind, status="pending")
            session.add(run)
            sch.last_run_at = now
            await session.flush()
            created.append(run.id)
        await session.commit()
    for run_id in created:
        await enqueue_crawl(run_id)
    return len(created)


async def boost_tick() -> int:
    """临近结束加密盯价：开启了 ending_boost 的站点有拍品即将结束时，按设定间隔创建只刷新这些拍品的任务。"""
    now = utcnow()
    created: list[int] = []
    async with SessionLocal() as session:
        window = int(await get_setting(session, "live.boost_window_minutes") or 30)
        every = float(await get_setting(session, "live.boost_interval_minutes") or 1)
        sources = (await session.scalars(select(Source).where(Source.enabled, Source.ending_boost))).all()
        for source in sources:
            ending = await session.scalar(
                select(Lot.id).where(
                    Lot.source_id == source.id, Lot.status == "live",
                    Lot.end_time > now, Lot.end_time <= now + timedelta(minutes=window),
                ).limit(1)
            )
            if not ending or not await session.scalar(select(Rule.id).where(Rule.source_id == source.id, Rule.is_active)):
                continue
            busy = await session.scalar(
                select(CrawlRun.id).where(CrawlRun.source_id == source.id, CrawlRun.status.in_(["pending", "running"]))
            )
            last = await session.scalar(
                select(func.max(CrawlRun.created_at)).where(CrawlRun.source_id == source.id, CrawlRun.kind == "ending")
            )
            if busy or (last and _aware(last) > now - timedelta(minutes=every) + timedelta(seconds=5)):
                continue
            run = CrawlRun(source_id=source.id, trigger="boost", kind="ending", status="pending")
            session.add(run)
            await session.flush()
            created.append(run.id)
        await session.commit()
    for run_id in created:
        await enqueue_crawl(run_id)
    return len(created)


async def ending_notice_tick() -> int:
    from app.subscriptions import check_ending_soon

    async with SessionLocal() as session:
        return await check_ending_soon(session)


async def run_scheduler(interval: float = 15) -> None:
    log.info("scheduler started")
    while True:
        for name, job in (("schedule", tick), ("boost", boost_tick), ("ending notice", ending_notice_tick)):
            try:
                n = await job()
                if n:
                    log.info("scheduler %s: %d", name, n)
            except Exception:
                log.exception("scheduler %s tick failed", name)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler())
