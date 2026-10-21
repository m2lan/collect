"""定时采集调度器。

Redis 模式下作为独立进程运行（`python -m app.scheduler`）；未配置 Redis 时随 API 进程启动。
"""

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import CrawlRun, Rule, Schedule, Source
from app.models.entities import utcnow
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


async def run_scheduler(interval: float = 15) -> None:
    log.info("scheduler started")
    while True:
        try:
            n = await tick()
            if n:
                log.info("scheduler created %d runs", n)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler())
