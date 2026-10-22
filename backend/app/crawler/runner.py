import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.extractor import entry_links, extract_captured, extract_detail, extract_page, page_urls
from app.crawler.fetchers import FetchContext, Fetched, Fetcher, LoginError, open_fetcher
from app.crawler.fetchers.base import USER_AGENTS
from app.ai.tagging import auto_tag_new
from app.crawler.errors import classify_error
from app.crawler.health import update_health
from app.crawler.throttle import domain_of, throttle
from app.crawler.pipeline import UpsertStats, upsert_lots
from app.db import SessionLocal
from app.models import CrawlRun, Lot, Rule, Source
from app.models.entities import ACTIVE_RUN_STATUSES, utcnow
from app.schemas.rule import FetchConfig, RuleConfig
from app.notify import notify
from app.security import decrypt_json, encrypt_json
from app.subscriptions import notify_after_crawl
from app.settings_store import get_setting

log = logging.getLogger(__name__)

RETRIES = 3
LIVE_LIMIT = 200


class RunLog:
    """采集过程日志，写入 crawl_run.log 供界面查看；同时承载停止信号（手动停止、超过时长上限）。"""

    def __init__(self, limit: int = 500):
        self.entries: list[dict[str, Any]] = []
        self.limit = limit
        self.stop_reason: str | None = None

    def stop(self, reason: str) -> None:
        """请求在当前页面完成后停止，已抓到的数据照常保存。"""
        if self.stop_reason is None:
            self.stop_reason = reason
            self.warn(f"{reason}，当前页面完成后停止")

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


def hard_timeout(fetcher: Fetcher) -> float:
    """单个页面的总时限（秒）：加载、等待元素、截获接口、滚动等步骤各自有超时，这里兜底防止浏览器卡死。"""
    cfg = getattr(fetcher, "fetch_config", None)
    return (cfg.timeout if cfg else 30) * 3 + 30


async def fetch_with_retry(
    fetcher: Fetcher, url: str, runlog: RunLog, method: str = "GET", body: dict[str, Any] | None = None,
    domain_interval: float = 0.0,
) -> Fetched:
    """失败自动重试（指数退避 1s、2s），登录失败不重试；每次请求前按域名限速。"""
    limit = hard_timeout(fetcher)
    for attempt in range(1, RETRIES + 1):
        try:
            waited = await throttle(url, domain_interval)
            if waited >= 1:
                runlog.info(f"按域名限速等待 {waited:.1f}s（{domain_of(url)}）")
            try:
                return await asyncio.wait_for(fetcher.fetch(url, method, body), limit)
            except TimeoutError:
                if hasattr(fetcher, "reset"):
                    await fetcher.reset()  # type: ignore[attr-defined]
                raise TimeoutError(f"页面 {limit:.0f}s 内没有加载完成（页面卡死或一直在跳转）") from None
        except LoginError:
            raise
        except Exception as exc:
            if attempt == RETRIES:
                raise
            runlog.warn(f"请求失败（第 {attempt} 次）：{type(exc).__name__}: {str(exc)[:200]}，{2 ** (attempt - 1)}s 后重试")
            await asyncio.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def _item_key(item: dict[str, Any], key_field: str) -> str:
    value = item.get(key_field)
    if value is None:
        value = json.dumps({k: str(v) for k, v in item.items()}, sort_keys=True, ensure_ascii=False)
    return str(value)


def _page_requests(start: str, config: RuleConfig) -> list[tuple[str, dict[str, Any] | None]] | None:
    """能预先算出的分页请求列表（url, POST body）；next_link 等需逐页发现的返回 None。"""
    fc, pg = config.fetch, config.pagination
    if pg.type == "page_param" and fc.method == "POST":
        return [(start, {**(fc.body or {}), str(pg.param): p}) for p in range(pg.start, pg.start + pg.max_pages)]
    urls = page_urls(start, pg, fc.method)
    if urls is None:
        return None
    return [(u, fc.body) for u in urls]


async def find_entry_links(ctx: FetchContext, config: RuleConfig, runlog: RunLog) -> list[str]:
    """打开入口页（专场列表），返回要逐个抓取的专场链接。"""
    entry = config.entry
    assert entry is not None
    fc = config.fetch
    # 入口页单独抓取：不截获接口、不滚动，避免等待列表接口超时
    fetch = FetchConfig(mode=fc.mode, wait_for=entry.wait_for, wait_ms=fc.wait_ms, timeout=fc.timeout)  # type: ignore[arg-type]
    started = time.monotonic()
    async with open_fetcher(ctx, fetch) as fetcher:
        fetched = await fetch_with_retry(fetcher, ctx.start_url, runlog, domain_interval=ctx.domain_interval)
    links = entry_links(fetched.html, fetched.url, entry)
    runlog.info(
        f"入口页 {ctx.start_url} → HTTP {fetched.status}，找到 {len(links)} 个专场链接"
        f"（选择器「{entry.link_selector}」），耗时 {time.monotonic() - started:.1f}s"
    )
    if not links:
        from app.ai.html_compress import compress_html

        runlog.warn(f"入口页没有找到专场链接，页面片段：{compress_html(fetched.html, 1500)}")
    elif len(links) > entry.max_links:
        runlog.info(f"专场数超过上限，只抓取前 {entry.max_links} 个")
    return links[: entry.max_links]


async def crawl(
    ctx: FetchContext,
    config: RuleConfig,
    rate_limit: float = 1.0,
    max_pages: int | None = None,
    runlog: RunLog | None = None,
    known_keys: KnownKeys | None = None,
    with_detail: bool = True,
    max_entries: int | None = None,
) -> CrawlResult:
    """按规则抓取所有页面并返回抽取结果（不入库），预览和正式采集共用。

    配置了入口页（entry）时，先从入口页找出各专场链接，再对每个专场按列表规则和翻页规则抓取；
    max_pages 是每个列表（专场）的页数上限，max_entries 限制进入的专场数（预览、AI 验证时用）。
    """
    runlog = runlog or RunLog()
    result = CrawlResult()
    fc = config.fetch
    limit = min(config.pagination.max_pages, max_pages or config.pagination.max_pages)
    if config.pagination.type == "none":
        limit = 1
    interval = 1 / rate_limit if rate_limit > 0 else 0
    item_keys: set[str] = set()

    if config.entry:
        starts = await find_entry_links(ctx, config, runlog)
        if max_entries:
            starts = starts[:max_entries]
        if not starts:
            return result
    else:
        starts = [fc.url or ctx.start_url]

    async with open_fetcher(ctx, fc, capture_json=bool(fc.capture)) as fetcher:
        first_error: Exception | None = None
        for i, start in enumerate(starts):
            if runlog.stop_reason:
                break
            if config.entry:
                runlog.info(f"专场 {i + 1}/{len(starts)}：{start}")
                if interval:
                    await asyncio.sleep(interval)
            try:
                await _crawl_list(fetcher, ctx, config, start, limit, interval, runlog, result, item_keys)
            except LoginError:
                raise
            except Exception as exc:
                if not config.entry:
                    raise
                # 某个专场失败不影响其他专场
                first_error = first_error or exc
                runlog.error(f"专场 {start} 抓取失败：{type(exc).__name__}: {str(exc)[:200]}")
        if first_error and not result.items:
            raise first_error

        if with_detail and config.detail.enabled and result.items:
            await _crawl_details(fetcher, config, result, runlog, interval, known_keys, ctx.domain_interval)
    return result


async def _crawl_list(
    fetcher: Fetcher, ctx: FetchContext, config: RuleConfig, start: str, limit: int, interval: float,
    runlog: RunLog, result: CrawlResult, item_keys: set[str],
) -> None:
    """从 start 开始抓取一个拍品列表的所有页面（含翻页），结果追加到 result。"""
    fc = config.fetch
    queue = _page_requests(start, config) or [(start, fc.body)]
    seen: set[str] = set()
    key_field = config.list.unique_key or "url"
    pages = 0
    clicked: Fetched | None = None  # 点击翻页得到的下一页内容
    click_started = 0.0
    while (queue or clicked) and pages < limit:
        if runlog.stop_reason:
            break
        started = click_started if clicked is not None else time.monotonic()
        if clicked is not None:
            fetched, url, body, clicked = clicked, clicked.url, None, None
            source = f"点击「{config.pagination.selector}」"
        else:
            url, body = queue.pop(0)
            key = f"{url}|{body}"
            if key in seen:
                continue
            seen.add(key)
            if pages and interval:
                await asyncio.sleep(interval)
            try:
                fetched = await fetch_with_retry(fetcher, url, runlog, fc.method, body, ctx.domain_interval)
            except LoginError:
                raise
            except Exception as exc:
                if not pages:
                    raise
                # 翻页中途失败：停止翻页，前面抓到的数据照常保存
                msg = f"第 {pages + 1} 页 {url} 抓取失败，停止翻页（已抓到的数据会保存）：{type(exc).__name__}: {str(exc)[:200]}"
                runlog.error(msg)
                result.errors.append(msg)
                break
            source = url + (f"（页码 {body.get(config.pagination.param)}）" if body and config.pagination.param in body else "")
        if fc.capture:
            page = extract_captured(fetched.json_responses, fetched.url, config)
        else:
            page = extract_page(fetched.html, fetched.url, config)
        pages += 1
        result.pages += 1
        new_items = []
        for it in page.items:  # 同一页内也要去重（有的页面会重复请求同一个列表接口）
            k = _item_key(it, key_field)
            if k not in item_keys:
                item_keys.add(k)
                new_items.append(it)
        result.items.extend(new_items)
        result.errors.extend(page.errors)
        runlog.info(
            f"第 {pages} 页 {source} → HTTP {fetched.status}，{len(fetched.html)} 字节，"
            f"抽取 {len(page.items)} 条（新 {len(new_items)} 条），耗时 {time.monotonic() - started:.1f}s"
        )
        for err in page.errors[:3]:
            runlog.warn(err)
        if not page.items:
            if pages > 1:
                runlog.info("该页没有拍品，停止翻页")
            elif not config.entry or not result.items:
                # 首页就抽不到数据时保存页面片段，便于判断是改版、被拦截还是未登录（专场为空时不重复记录）
                from app.ai.html_compress import compress_html

                snippet = fetched.html[:1500] if config.fetch.mode == "api" else compress_html(fetched.html, 1500)
                runlog.warn(f"页面片段（{fetched.url}）：{snippet}")
            break
        if pages > 1 and not new_items:
            runlog.info("翻页后没有新的拍品（页面内容未变化），停止翻页")
            break
        if page.next_url and f"{page.next_url}|None" not in seen:
            queue.append((page.next_url, None))
        if config.pagination.type == "click" and pages < limit:
            if not hasattr(fetcher, "click_next"):
                runlog.warn("点击翻页需要浏览器模式，请把抓取方式改为浏览器渲染")
                break
            if interval:
                await asyncio.sleep(interval)
            await throttle(fetched.url, ctx.domain_interval)
            click_started = time.monotonic()
            try:
                clicked = await asyncio.wait_for(
                    fetcher.click_next(config.pagination.selector or ""), hard_timeout(fetcher)  # type: ignore[attr-defined]
                )
            except TimeoutError:
                msg = f"点击「{config.pagination.selector}」后页面 {hard_timeout(fetcher):.0f}s 内没有响应，停止翻页（已抓到的数据会保存）"
                runlog.error(msg)
                result.errors.append(msg)
                break
            if clicked is None:
                runlog.info(f"下一页按钮「{config.pagination.selector}」不存在或已禁用（已是最后一页），停止翻页")
    if pages >= limit and (queue or clicked):
        runlog.info(f"已达到最大页数 {limit}，停止翻页")


async def _crawl_details(
    fetcher: Fetcher, config: RuleConfig, result: CrawlResult, runlog: RunLog, interval: float,
    known_keys: KnownKeys | None, domain_interval: float = 0.0,
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
        if runlog.stop_reason:
            break
        if interval:
            await asyncio.sleep(interval)
        url = str(it[detail.url_field])
        try:
            fetched = await fetch_with_retry(fetcher, url, runlog, domain_interval=domain_interval)
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


async def apply_network(session: AsyncSession, ctx: FetchContext, use_proxy: bool) -> FetchContext:
    """把代理、UA、指纹伪装、按域名限速等反爬设置应用到抓取上下文。"""
    ctx.proxy, ctx.user_agent = await network_options(session, use_proxy)
    if ctx.auth_config.get("user_agent"):
        ctx.user_agent = ctx.auth_config["user_agent"]  # 人工登录会话与登录时的 UA 绑定
    ctx.stealth = bool(await get_setting(session, "crawler.stealth"))
    ctx.domain_interval = float(await get_setting(session, "crawler.domain_interval") or 0)
    return ctx


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
    session: AsyncSession, source: Source, config: RuleConfig, ctx: FetchContext, run: CrawlRun, runlog: RunLog,
    ending_window: int | None = None,
) -> tuple[int, int, UpsertStats]:
    """盯价：只刷新进行中的拍品（ending_window 给定时只刷新该分钟数内结束的）。
    配置了详情页时逐个抓取详情，否则重新抓取列表页。返回 (页数/详情数, 抓取数, 入库统计)。"""
    now = utcnow()
    stmt = select(Lot).where(Lot.source_id == source.id, Lot.status == "live")
    if ending_window:
        stmt = stmt.where(Lot.end_time > now - timedelta(minutes=5), Lot.end_time <= now + timedelta(minutes=ending_window))
    else:
        stmt = stmt.where(or_(Lot.end_time.is_(None), Lot.end_time > now - timedelta(hours=1)))
    lots = (await session.scalars(stmt.order_by(Lot.end_time.asc().nulls_last()).limit(LIVE_LIMIT))).all()
    what = f"{ending_window} 分钟内结束" if ending_window else "进行中"
    runlog.info(f"盯价：找到 {len(lots)} 个{what}的拍品")
    if not lots:
        return 0, 0, UpsertStats()

    if not config.detail.enabled:
        runlog.info("规则未配置详情页，重新抓取列表页以刷新价格")
        result = await crawl(ctx, config, source.rate_limit, runlog=runlog, with_detail=False)
        live_keys = {lot.unique_key for lot in lots}
        key_field = config.list.unique_key or "url"
        items = [it for it in result.items if str(it.get(key_field) or it.get("url")) in live_keys]
        stats = await upsert_lots(session, source.id, run.id, items, config.list.unique_key)
        return result.pages, len(items), stats

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
                fetched = await fetch_with_retry(fetcher, str(url), runlog, domain_interval=ctx.domain_interval)
            except LoginError:
                raise
            except Exception as exc:
                runlog.warn(f"详情页抓取失败 {url}：{type(exc).__name__}: {str(exc)[:200]}")
                continue
            values, _ = extract_detail(fetched.html, fetched.url, config.detail)
            items.append({**{k: v for k, v in values.items() if v is not None}, key_field: lot.unique_key, "url": lot.url})
    stats = await upsert_lots(session, source.id, run.id, items, key_field)
    return len(items), len(items), stats


MANUAL_STOP = "已手动停止"
HEARTBEAT_SECONDS = 5


async def _heartbeat(run_id: int, runlog: RunLog, max_minutes: float) -> None:
    """执行期间定期写入实时日志与心跳，并响应"停止"请求和时长上限。"""
    started = time.monotonic()
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if max_minutes and time.monotonic() - started > max_minutes * 60:
            runlog.stop(f"已达到单次采集时长上限 {max_minutes:g} 分钟")
        try:
            async with SessionLocal() as s:
                status = await s.scalar(select(CrawlRun.status).where(CrawlRun.id == run_id))
                if status == "cancelling":
                    runlog.stop(MANUAL_STOP)
                await s.execute(
                    update(CrawlRun).where(CrawlRun.id == run_id).values(log=list(runlog.entries), heartbeat_at=utcnow())
                )
                await s.commit()
        except Exception:
            log.warning("heartbeat for run %s failed", run_id, exc_info=True)


async def _mark_interrupted(run_id: int, runlog: RunLog, reason: str) -> None:
    runlog.error(reason)
    async with SessionLocal() as s:
        await s.execute(
            update(CrawlRun).where(CrawlRun.id == run_id, CrawlRun.status.in_(ACTIVE_RUN_STATUSES))
            .values(status="failed", error=reason, error_type=classify_error(reason), finished_at=utcnow(), log=runlog.entries)
        )
        await s.commit()


async def run_crawl(run_id: int) -> None:
    runlog = RunLog()
    heartbeat: asyncio.Task | None = None
    try:
        async with SessionLocal() as session:
            run = await session.get(CrawlRun, run_id)
            if run is None or run.status != "pending":
                return  # 已被取消或已由其他进程执行
            max_minutes = float(await get_setting(session, "crawler.max_run_minutes") or 0)
            run.status, run.started_at, run.heartbeat_at = "running", utcnow(), utcnow()
            await session.commit()
            heartbeat = asyncio.create_task(_heartbeat(run_id, runlog, max_minutes))
            await _execute_run(session, run, runlog, max_minutes)
    except asyncio.CancelledError:
        # arq 任务超时或 worker 关闭：标记为中断，避免一直显示"采集中"
        await asyncio.shield(_mark_interrupted(run_id, runlog, "任务被中断（超过任务时限或 worker 停止）"))
        raise
    except Exception as exc:
        # 收尾阶段（写库、通知等）出错时也不能让任务停留在执行中
        log.exception("crawl run %s crashed", run_id)
        await _mark_interrupted(run_id, runlog, f"任务异常结束：{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        if heartbeat:
            heartbeat.cancel()


async def _execute_run(session: AsyncSession, run: CrawlRun, runlog: RunLog, max_minutes: float) -> None:
    run_id = run.id
    items: list[dict[str, Any]] | None = None
    stats: UpsertStats | None = None
    source = await session.get(Source, run.source_id)
    rule = await session.scalar(select(Rule).where(Rule.source_id == run.source_id, Rule.is_active))
    try:
        if source is None or rule is None:
            raise RuntimeError("站点不存在或没有启用的规则")
        run.rule_version = rule.version
        # 立即提交：采集期间不能持有 crawl_run 行的写锁，否则心跳与"停止"请求会一直等待
        await session.commit()
        config = RuleConfig.model_validate(rule.config)
        ctx = await apply_network(session, context_for(source), source.use_proxy)
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

        if run.kind in ("live", "ending"):
            window = int(await get_setting(session, "live.boost_window_minutes") or 30) if run.kind == "ending" else None
            run.pages, run.items_found, stats = await _live_refresh(session, source, config, ctx, run, runlog, window)
            run.items_updated = stats.updated
            run.status = "success"
            runlog.info(f"盯价完成：刷新 {run.items_found} 个拍品，价格/状态变化 {run.items_updated} 个")
        else:
            async def known(keys: list[str]) -> set[str]:
                rows = await session.scalars(
                    select(Lot.unique_key).where(Lot.source_id == source.id, Lot.unique_key.in_(keys))
                )
                return set(rows.all())

            # 正常情况下由心跳在时长上限处优雅停止；这里再留 5 分钟兜底，防止卡在单个操作上
            async with asyncio.timeout(max_minutes * 60 + 300 if max_minutes else None):
                result = await crawl(ctx, config, source.rate_limit, runlog=runlog, known_keys=known)
            items = result.items
            stats = await upsert_lots(session, source.id, run.id, result.items, config.list.unique_key)
            run.pages = result.pages
            run.items_found = len(result.items)
            run.items_new, run.items_updated = stats.new, stats.updated
            await session.commit()
            runlog.info(f"入库完成：抓取 {len(result.items)} 条，新增 {stats.new} 条，价格/状态变化 {stats.updated} 条")
            run.status = "success" if result.items else "failed"
            if runlog.stop_reason == MANUAL_STOP:
                run.status, run.error = "cancelled", f"{MANUAL_STOP}，已保存 {len(result.items)} 条"
            elif not result.items:
                run.error = runlog.stop_reason or "未抽取到任何拍品，请检查规则或登录状态"
                runlog.error(run.error)
            elif result.errors or runlog.stop_reason:
                run.error = "\n".join(([runlog.stop_reason] if runlog.stop_reason else []) + result.errors[:20])
    except LoginError as exc:
        await session.rollback()
        run = await session.get(CrawlRun, run_id)
        assert run
        run.status, run.error = "failed", str(exc)[:4000]
        runlog.error(run.error)
        src = await session.get(Source, run.source_id)
        if src is not None and src.login_expired_at is None:
            src.login_expired_at = utcnow()
            await notify(session, f"登录失效：{src.name}", f"{exc}\n站点：{src.start_url}")
    except Exception as exc:
        log.exception("crawl run %s failed", run_id)
        await session.rollback()
        run = await session.get(CrawlRun, run_id)
        assert run
        detail = str(exc) or ("采集超过时长上限后仍未结束，已强制终止" if isinstance(exc, TimeoutError) else "")
        run.status, run.error = "failed", f"{type(exc).__name__}: {detail}"[:4000]
        runlog.error(run.error)
    run.finished_at = utcnow()
    if run.started_at:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    run.error_type = classify_error(run.error) if run.status == "failed" else None
    if run.status == "success":
        src = await session.get(Source, run.source_id)
        if src is not None and src.login_expired_at is not None:
            src.login_expired_at = None
            runlog.info("登录状态已恢复正常")
            await notify(session, f"登录已恢复：{src.name}", "采集恢复正常")
    needs_repair = False
    source = await session.get(Source, run.source_id)
    # 盯价任务只刷新已有拍品，不参与规则健康判定
    if source is not None and run.kind == "full" and run.status != "cancelled":
        needs_repair = await update_health(session, source, run, items)
        if source.health != "ok":
            runlog.warn(f"规则健康状态：{source.health}，{source.health_message}")
    run.log = runlog.entries
    await session.commit()
    if stats is not None:
        try:
            # 先标注新拍品，订阅中按标签筛选才能命中
            await auto_tag_new(stats.new_ids)
        except Exception:
            log.exception("auto tag failed for run %s", run_id)
        try:
            await notify_after_crawl(session, stats.new_ids, stats.price_changes)
        except Exception:
            log.exception("subscription notify failed for run %s", run_id)
    if needs_repair and source is not None:
        await maybe_auto_repair(source.id)
