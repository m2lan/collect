import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.crawler.extractor import extract_page, page_urls
from app.crawler.fetchers import FetchContext, open_fetcher
from app.crawler.pipeline import upsert_lots
from app.db import SessionLocal
from app.models import CrawlRun, Rule, Source
from app.models.entities import utcnow
from app.schemas.rule import RuleConfig
from app.security import decrypt_json, encrypt_json

log = logging.getLogger(__name__)


class RunLog:
    """采集过程日志，写入 crawl_run.log 供界面查看。"""

    def __init__(self, limit: int = 500):
        self.entries: list[dict[str, Any]] = []
        self.limit = limit

    def add(self, msg: str, level: str = "info") -> None:
        log.log(logging.WARNING if level != "info" else logging.INFO, msg)
        if len(self.entries) < self.limit:
            self.entries.append({"t": utcnow().isoformat(timespec="seconds"), "level": level, "msg": msg})

    def info(self, msg: str) -> None:
        self.add(msg)

    def warn(self, msg: str) -> None:
        self.add(msg, "warn")

    def error(self, msg: str) -> None:
        self.add(msg, "error")


@dataclass
class CrawlResult:
    pages: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def crawl(
    ctx: FetchContext, config: RuleConfig, rate_limit: float = 1.0, max_pages: int | None = None,
    runlog: RunLog | None = None,
) -> CrawlResult:
    """按规则抓取所有页面并返回抽取结果（不入库），预览和正式采集共用。"""
    runlog = runlog or RunLog()
    result = CrawlResult()
    limit = min(config.pagination.max_pages, max_pages or config.pagination.max_pages)
    if config.pagination.type == "none":
        limit = 1
    queue = page_urls(ctx.start_url, config.pagination) or [ctx.start_url]
    seen: set[str] = set()
    interval = 1 / rate_limit if rate_limit > 0 else 0

    async with open_fetcher(ctx, config.fetch) as fetcher:
        while queue and result.pages < limit:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if result.pages and interval:
                await asyncio.sleep(interval)
            started = time.monotonic()
            fetched = await fetcher.fetch(url)
            page = extract_page(fetched.html, fetched.url, config)
            result.pages += 1
            result.items.extend(page.items)
            result.errors.extend(page.errors)
            runlog.info(
                f"第 {result.pages} 页 {url} → HTTP {fetched.status}，{len(fetched.html)} 字节，"
                f"抽取 {len(page.items)} 条，耗时 {time.monotonic() - started:.1f}s"
            )
            for err in page.errors[:3]:
                runlog.warn(err)
            if not page.items:
                if result.pages > 1:
                    runlog.info("该页没有拍品，停止翻页")
                break
            if page.next_url and page.next_url not in seen:
                queue.append(page.next_url)
        if result.pages >= limit and queue:
            runlog.info(f"已达到最大页数 {limit}，停止翻页")
    return result


def context_for(source: Source) -> FetchContext:
    return FetchContext(
        start_url=source.start_url,
        fetch_mode=source.fetch_mode,
        headers=source.headers or {},
        auth_type=source.auth_type,
        auth_config=decrypt_json(source.auth_config) or {},
        storage_state=decrypt_json(source.storage_state),
    )


async def run_crawl(run_id: int) -> None:
    runlog = RunLog()
    async with SessionLocal() as session:
        run = await session.get(CrawlRun, run_id)
        if run is None:
            return
        source = await session.get(Source, run.source_id)
        rule = await session.scalar(select(Rule).where(Rule.source_id == run.source_id, Rule.is_active))
        run.status, run.started_at = "running", utcnow()
        await session.commit()

        try:
            if source is None or rule is None:
                raise RuntimeError("站点不存在或没有启用的规则")
            run.rule_version = rule.version
            config = RuleConfig.model_validate(rule.config)
            ctx = context_for(source)
            runlog.info(f"开始采集：{source.name}，规则 v{rule.version}，抓取方式 {config.fetch.mode or source.fetch_mode}")

            async def save_state(state: dict[str, Any]) -> None:
                runlog.info("自动登录成功，已保存登录会话")
                source.storage_state = encrypt_json(state)
                await session.commit()

            ctx.on_storage_state = save_state
            result = await crawl(ctx, config, source.rate_limit, runlog=runlog)
            stats = await upsert_lots(session, source.id, run.id, result.items, config.list.unique_key)
            run.pages = result.pages
            run.items_found = len(result.items)
            run.items_new, run.items_updated = stats.new, stats.updated
            runlog.info(f"入库完成：抓取 {len(result.items)} 条，新增 {stats.new} 条，价格/状态变化 {stats.updated} 条")
            run.status = "success" if result.items else "failed"
            if not result.items:
                run.error = "未抽取到任何拍品，请检查规则或登录状态"
                runlog.error(run.error)
            elif result.errors:
                run.error = "\n".join(result.errors[:20])
        except Exception as exc:
            log.exception("crawl run %s failed", run_id)
            await session.rollback()
            run = await session.get(CrawlRun, run_id)
            assert run
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:4000]
            runlog.error(run.error)
        run.finished_at = utcnow()
        run.log = runlog.entries
        await session.commit()
