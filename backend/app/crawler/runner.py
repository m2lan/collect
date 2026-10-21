import asyncio
import logging
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


@dataclass
class CrawlResult:
    pages: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def crawl(ctx: FetchContext, config: RuleConfig, rate_limit: float = 1.0, max_pages: int | None = None) -> CrawlResult:
    """按规则抓取所有页面并返回抽取结果（不入库），预览和正式采集共用。"""
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
            fetched = await fetcher.fetch(url)
            page = extract_page(fetched.html, fetched.url, config)
            result.pages += 1
            result.items.extend(page.items)
            result.errors.extend(page.errors)
            if not page.items:
                break
            if page.next_url and page.next_url not in seen:
                queue.append(page.next_url)
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

            async def save_state(state: dict[str, Any]) -> None:
                source.storage_state = encrypt_json(state)
                await session.commit()

            ctx.on_storage_state = save_state
            result = await crawl(ctx, config, source.rate_limit)
            stats = await upsert_lots(session, source.id, run.id, result.items, config.list.unique_key)
            run.pages = result.pages
            run.items_found = len(result.items)
            run.items_new, run.items_updated = stats.new, stats.updated
            run.status = "success" if result.items else "failed"
            if not result.items:
                run.error = "未抽取到任何拍品，请检查规则或登录状态"
            elif result.errors:
                run.error = "\n".join(result.errors[:20])
        except Exception as exc:
            log.exception("crawl run %s failed", run_id)
            await session.rollback()
            run = await session.get(CrawlRun, run_id)
            assert run
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:4000]
        run.finished_at = utcnow()
        await session.commit()
