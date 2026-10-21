import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.extractor import extract_detail, extract_page, page_urls
from app.crawler.fetchers import FetchContext, Fetched, Fetcher, LoginError, open_fetcher
from app.crawler.fetchers.base import USER_AGENTS
from app.crawler.health import update_health
from app.crawler.pipeline import upsert_lots
from app.db import SessionLocal
from app.models import CrawlRun, Lot, Rule, Source
from app.models.entities import utcnow
from app.schemas.rule import RuleConfig
from app.security import decrypt_json, encrypt_json
from app.settings_store import get_setting

log = logging.getLogger(__name__)

RETRIES = 3
LIVE_LIMIT = 200


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
    details: int = 0


KnownKeys = Callable[[list[str]], Awaitable[set[str]]]


async def fetch_with_retry(
    fetcher: Fetcher, url: str, runlog: RunLog, method: str = "GET", body: dict[str, Any] | None = None
) -> Fetched:
    """失败自动重试（指数退避 1s、2s），登录失败不重试。"""
    for attempt in range(1, RETRIES + 1):
        try:
            return await fetcher.fetch(url, method, body)
        except LoginError:
            raise
        except Exception as exc:
            if attempt == RETRIES:
                raise
            runlog.warn(f"请求失败（第 {attempt} 次）：{type(exc).__name__}: {str(exc)[:200]}，{2 ** (attempt - 1)}s 后重试")
            await asyncio.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def _page_requests(ctx: FetchContext, config: RuleConfig) -> list[tuple[str, dict[str, Any] | None]] | None:
    """能预先算出的分页请求列表（url, POST body）；next_link 等需逐页发现的返回 None。"""
    fc, pg = config.fetch, config.pagination
    start = fc.url or ctx.start_url
    if pg.type == "page_param" and fc.method == "POST":
        return [(start, {**(fc.body or {}), str(pg.param): p}) for p in range(pg.start, pg.start + pg.max_pages)]
    urls = page_urls(start, pg, fc.method)
    if urls is None:
        return None
    return [(u, fc.body) for u in urls]


async def crawl(
    ctx: FetchContext,
    config: RuleConfig,
    rate_limit: float = 1.0,
    max_pages: int | None = None,
    runlog: RunLog | None = None,
    known_keys: KnownKeys | None = None,
    with_detail: bool = True,
) -> CrawlResult:
    """按规则抓取所有页面并返回抽取结果（不入库），预览和正式采集共用。"""
    runlog = runlog or RunLog()
    result = CrawlResult()
    fc = config.fetch
    limit = min(config.pagination.max_pages, max_pages or config.pagination.max_pages)
    if config.pagination.type == "none":
        limit = 1
    queue = _page_requests(ctx, config) or [(fc.url or ctx.start_url, fc.body)]
    seen: set[str] = set()
    interval = 1 / rate_limit if rate_limit > 0 else 0

    async with open_fetcher(ctx, fc) as fetcher:
        while queue and result.pages < limit:
            url, body = queue.pop(0)
            key = f"{url}|{body}"
            if key in seen:
                continue
            seen.add(key)
            if result.pages and interval:
                await asyncio.sleep(interval)
            started = time.monotonic()
            fetched = await fetch_with_retry(fetcher, url, runlog, fc.method, body)
            page = extract_page(fetched.html, fetched.url, config)
            result.pages += 1
            result.items.extend(page.items)
            result.errors.extend(page.errors)
            page_no = f"（页码 {body.get(config.pagination.param)}）" if body and config.pagination.param in body else ""
            runlog.info(
                f"第 {result.pages} 页 {url}{page_no} → HTTP {fetched.status}，{len(fetched.html)} 字节，"
                f"抽取 {len(page.items)} 条，耗时 {time.monotonic() - started:.1f}s"
            )
            for err in page.errors[:3]:
                runlog.warn(err)
            if not page.items:
                if result.pages > 1:
                    runlog.info("该页没有拍品，停止翻页")
                else:
                    # 首页就抽不到数据时保存页面片段，便于判断是改版、被拦截还是未登录
                    from app.ai.html_compress import compress_html

                    snippet = fetched.html[:1500] if config.fetch.mode == "api" else compress_html(fetched.html, 1500)
                    runlog.warn(f"页面片段（{fetched.url}）：{snippet}")
                break
            if page.next_url and f"{page.next_url}|None" not in seen:
                queue.append((page.next_url, None))
        if result.pages >= limit and queue:
            runlog.info(f"已达到最大页数 {limit}，停止翻页")

        if with_detail and config.detail.enabled and result.items:
            await _crawl_details(fetcher, config, result, runlog, interval, known_keys)
    return result


async def _crawl_details(
    fetcher: Fetcher, config: RuleConfig, result: CrawlResult, runlog: RunLog, interval: float,
    known_keys: KnownKeys | None,
) -> None:
    detail = config.detail
    key_field = config.list.unique_key or "url"
    candidates = [it for it in result.items if it.get(detail.url_field)]
    if detail.only_new and known_keys:
        known = await known_keys([str(it.get(key_field)) for it in candidates if it.get(key_field) is not None])
        candidates = [it for it in candidates if str(it.get(key_field)) not in known]
    candidates = candidates[: detail.max_items]
    if not candidates:
        runlog.info("没有需要抓取详情页的拍品")
        return
    runlog.info(f"开始抓取 {len(candidates)} 个详情页")
    failed = 0
    for it in candidates:
        if interval:
            await asyncio.sleep(interval)
        url = str(it[detail.url_field])
        try:
            fetched = await fetch_with_retry(fetcher, url, runlog)
        except LoginError:
            raise
        except Exception as exc:
            failed += 1
            runlog.warn(f"详情页抓取失败 {url}：{type(exc).__name__}: {str(exc)[:200]}")
            continue
        values, errors = extract_detail(fetched.html, fetched.url, detail)
        result.errors.extend(errors)
        it.update({k: v for k, v in values.items() if v is not None})
        result.details += 1
    runlog.info(f"详情页完成：成功 {result.details}，失败 {failed}")


async def network_options(session: AsyncSession, use_proxy: bool) -> tuple[str | None, str]:
    """根据系统设置选择代理（随机）与 User-Agent。"""
    proxy = None
    if use_proxy:
        proxies = [p.strip() for p in (await get_setting(session, "crawler.proxies") or []) if p.strip()]
        proxy = random.choice(proxies) if proxies else None
    ua = random.choice(USER_AGENTS) if await get_setting(session, "crawler.random_ua") else USER_AGENTS[0]
    return proxy, ua


def context_for(source: Source) -> FetchContext:
    return FetchContext(
        start_url=source.start_url,
        fetch_mode=source.fetch_mode,
        headers=source.headers or {},
        auth_type=source.auth_type,
        auth_config=decrypt_json(source.auth_config) or {},
        storage_state=decrypt_json(source.storage_state),
    )


async def maybe_auto_repair(source_id: int) -> None:
    from app.ai.repair import auto_repair

    try:
        await auto_repair(source_id)
    except Exception:
        log.exception("auto repair for source %s failed", source_id)


async def _live_refresh(
    session: AsyncSession, source: Source, config: RuleConfig, ctx: FetchContext, run: CrawlRun, runlog: RunLog
) -> tuple[int, int, int]:
    """盯价：只刷新进行中的拍品。配置了详情页时逐个抓取详情，否则重新抓取列表页。返回 (页数/详情数, 抓取数, 变化数)。"""
    now = utcnow()
    lots = (
        await session.scalars(
            select(Lot)
            .where(Lot.source_id == source.id, Lot.status == "live")
            .where(or_(Lot.end_time.is_(None), Lot.end_time > now - timedelta(hours=1)))
            .order_by(Lot.end_time.asc().nulls_last())
            .limit(LIVE_LIMIT)
        )
    ).all()
    runlog.info(f"盯价：找到 {len(lots)} 个进行中的拍品")
    if not lots:
        return 0, 0, 0

    if not config.detail.enabled:
        runlog.info("规则未配置详情页，重新抓取列表页以刷新价格")
        result = await crawl(ctx, config, source.rate_limit, runlog=runlog, with_detail=False)
        live_keys = {lot.unique_key for lot in lots}
        key_field = config.list.unique_key or "url"
        items = [it for it in result.items if str(it.get(key_field) or it.get("url")) in live_keys]
        stats = await upsert_lots(session, source.id, run.id, items, config.list.unique_key)
        return result.pages, len(items), stats.updated

    key_field = config.list.unique_key or "url"
    items: list[dict[str, Any]] = []
    interval = 1 / source.rate_limit if source.rate_limit > 0 else 0
    fetch_cfg = config.fetch.model_copy(update={"mode": None if config.fetch.mode == "api" else config.fetch.mode})
    async with open_fetcher(ctx, fetch_cfg) as fetcher:
        for i, lot in enumerate(lots):
            field_name = config.detail.url_field
            url = lot.url if field_name == "url" else (lot.extra or {}).get(field_name)
            if not url:
                continue
            if i and interval:
                await asyncio.sleep(interval)
            try:
                fetched = await fetch_with_retry(fetcher, str(url), runlog)
            except LoginError:
                raise
            except Exception as exc:
                runlog.warn(f"详情页抓取失败 {url}：{type(exc).__name__}: {str(exc)[:200]}")
                continue
            values, _ = extract_detail(fetched.html, fetched.url, config.detail)
            items.append({**{k: v for k, v in values.items() if v is not None}, key_field: lot.unique_key, "url": lot.url})
    stats = await upsert_lots(session, source.id, run.id, items, key_field)
    return len(items), len(items), stats.updated


async def run_crawl(run_id: int) -> None:
    runlog = RunLog()
    items: list[dict[str, Any]] | None = None
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
            ctx.proxy, ctx.user_agent = await network_options(session, source.use_proxy)
            mode = config.fetch.mode or source.fetch_mode
            runlog.info(
                f"开始{'盯价' if run.kind == 'live' else '采集'}：{source.name}，规则 v{rule.version}，抓取方式 {mode}"
                + (f"，代理 {ctx.proxy.split('@')[-1]}" if ctx.proxy else "")
            )

            async def save_state(state: dict[str, Any]) -> None:
                runlog.info("自动登录成功，已保存登录会话")
                source.storage_state = encrypt_json(state)
                await session.commit()

            ctx.on_storage_state = save_state

            if run.kind == "live":
                run.pages, run.items_found, run.items_updated = await _live_refresh(session, source, config, ctx, run, runlog)
                run.status = "success"
                runlog.info(f"盯价完成：刷新 {run.items_found} 个拍品，价格/状态变化 {run.items_updated} 个")
            else:
                async def known(keys: list[str]) -> set[str]:
                    rows = await session.scalars(
                        select(Lot.unique_key).where(Lot.source_id == source.id, Lot.unique_key.in_(keys))
                    )
                    return set(rows.all())

                result = await crawl(ctx, config, source.rate_limit, runlog=runlog, known_keys=known)
                items = result.items
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
        needs_repair = False
        source = await session.get(Source, run.source_id)
        # 盯价任务只刷新已有拍品，不参与规则健康判定
        if source is not None and run.kind != "live":
            needs_repair = await update_health(session, source, run, items)
            if source.health != "ok":
                runlog.warn(f"规则健康状态：{source.health}，{source.health_message}")
        run.log = runlog.entries
        await session.commit()
        if needs_repair and source is not None:
            await maybe_auto_repair(source.id)
