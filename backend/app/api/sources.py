from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.api.deps import SessionDep
from app.crawler.cookies import parse_cookies
from app.crawler.fetchers import FetchContext, LoginError
from app.crawler.pipeline import jsonable
from app.crawler.fetchers import open_fetcher
from app.crawler.runner import RunLog, apply_network, crawl
from app.crawler.snapshot import sanitize_snapshot
from app.schemas.rule import FetchConfig
from app.models import CrawlRun, Lot, Rule, Schedule, Source
from app.schemas.api import (
    AuthIn,
    AuthOut,
    PreviewIn,
    PreviewOut,
    RuleIn,
    RuleOut,
    RunOut,
    ScheduleIn,
    ScheduleOut,
    SnapshotIn,
    SourceIn,
    SourceOut,
)
from app.security import decrypt_json, encrypt_json
from app.tasks import enqueue_crawl

router = APIRouter(prefix="/sources", tags=["sources"])
preview_router = APIRouter(tags=["preview"])

_FORM_KEYS = (
    "login_url", "username", "password", "username_selector",
    "password_selector", "submit_selector", "logged_in_selector",
)


def merge_auth(auth_type: str, incoming: AuthIn, stored: dict[str, Any] | None) -> dict[str, Any]:
    stored = stored or {}

    def pick(*keys: str) -> dict[str, Any]:
        merged = {k: stored.get(k) for k in keys}
        for k in keys:
            v = getattr(incoming, k, None)
            if v:  # 空值保留原配置（编辑时密码不回显）
                merged[k] = v
        return merged

    # 登录页地址 / 已登录标志用于检测会话是否失效
    markers = ("login_url", "logged_in_selector")
    if auth_type == "cookie":
        cookies = parse_cookies(incoming.cookies) if incoming.cookies else stored.get("cookies", [])
        return {"cookies": cookies, **pick(*markers)}
    if auth_type == "form":
        return pick(*_FORM_KEYS)
    if auth_type == "session":
        # 人工登录：浏览器状态单独保存；记住登录时的 User-Agent（Cloudflare 等验证与 UA 绑定）
        return {**pick(*markers), "user_agent": stored.get("user_agent"), "saved_at": stored.get("saved_at")}
    return {}


def _auth_out(source: Source) -> AuthOut:
    cfg = decrypt_json(source.auth_config) or {}
    return AuthOut(
        cookie_count=len(cfg.get("cookies", [])),
        login_url=cfg.get("login_url"),
        username=cfg.get("username"),
        has_password=bool(cfg.get("password")),
        username_selector=cfg.get("username_selector"),
        password_selector=cfg.get("password_selector"),
        submit_selector=cfg.get("submit_selector"),
        logged_in_selector=cfg.get("logged_in_selector"),
        has_session=bool(source.storage_state),
        session_saved_at=cfg.get("saved_at"),
    )


async def _source_out(session: SessionDep, source: Source) -> SourceOut:
    rule = await session.scalar(select(Rule).where(Rule.source_id == source.id, Rule.is_active))
    lot_count = await session.scalar(select(func.count()).select_from(Lot).where(Lot.source_id == source.id))
    last_run = await session.scalar(
        select(CrawlRun).where(CrawlRun.source_id == source.id).order_by(CrawlRun.id.desc()).limit(1)
    )
    return SourceOut(
        id=source.id, name=source.name, start_url=source.start_url, fetch_mode=source.fetch_mode,
        headers=source.headers or {}, auth_type=source.auth_type, rate_limit=source.rate_limit,
        enabled=source.enabled, allow_ai_repair=source.allow_ai_repair, use_proxy=source.use_proxy,
        ending_boost=source.ending_boost,
        created_at=source.created_at,
        auth=_auth_out(source), health=source.health, health_message=source.health_message,
        login_expired_at=source.login_expired_at,
        active_rule=rule.config if rule else None, active_rule_version=rule.version if rule else None,
        lot_count=lot_count or 0, last_run=RunOut.model_validate(last_run) if last_run else None,
    )


async def _get_source(session: SessionDep, source_id: int) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
    return source


async def _add_rule(session: SessionDep, source_id: int, body: RuleIn, origin: str = "manual") -> Rule:
    last = await session.scalar(select(func.max(Rule.version)).where(Rule.source_id == source_id))
    if body.activate:
        await session.execute(update(Rule).where(Rule.source_id == source_id).values(is_active=False))
    rule = Rule(
        source_id=source_id, version=(last or 0) + 1, config=body.config.model_dump(by_alias=True),
        is_active=body.activate, origin=origin, note=body.note,
    )
    session.add(rule)
    await session.flush()
    return rule


@router.get("", response_model=list[SourceOut])
async def list_sources(session: SessionDep) -> list[SourceOut]:
    sources = (await session.scalars(select(Source).order_by(Source.id.desc()))).all()
    return [await _source_out(session, s) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: int, session: SessionDep) -> SourceOut:
    return await _source_out(session, await _get_source(session, source_id))


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceIn, session: SessionDep) -> SourceOut:
    source = Source(
        name=body.name, start_url=str(body.start_url), fetch_mode=body.fetch_mode, headers=body.headers,
        auth_type=body.auth_type, rate_limit=body.rate_limit, enabled=body.enabled,
        allow_ai_repair=body.allow_ai_repair, use_proxy=body.use_proxy, ending_boost=body.ending_boost, auth_config=encrypt_json(merge_auth(body.auth_type, body.auth, None)),
    )
    session.add(source)
    await session.flush()
    if body.rule:
        await _add_rule(session, source.id, RuleIn(config=body.rule), body.rule_origin)
    await session.commit()
    return await _source_out(session, source)


@router.put("/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, body: SourceIn, session: SessionDep) -> SourceOut:
    source = await _get_source(session, source_id)
    old_auth = decrypt_json(source.auth_config) or {}
    stored = old_auth if body.auth_type == source.auth_type else None
    new_auth = merge_auth(body.auth_type, body.auth, stored)
    credentials = ("login_url", "username", "password")
    if body.auth_type != source.auth_type or (
        body.auth_type == "form" and any(new_auth.get(k) != old_auth.get(k) for k in credentials)
    ):
        source.storage_state = None  # 登录方式或账号变化后旧会话失效
    source.auth_config = encrypt_json(new_auth)
    for field in ("name", "fetch_mode", "headers", "auth_type", "rate_limit", "enabled", "allow_ai_repair", "use_proxy", "ending_boost"):
        setattr(source, field, getattr(body, field))
    source.start_url = str(body.start_url)
    if body.rule:
        active = await session.scalar(select(Rule).where(Rule.source_id == source_id, Rule.is_active))
        if active is None or active.config != body.rule.model_dump(by_alias=True):
            await _add_rule(session, source_id, RuleIn(config=body.rule), body.rule_origin)
            source.health, source.health_message, source.consecutive_failures = "ok", None, 0
    await session.commit()
    return await _source_out(session, source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: int, session: SessionDep) -> None:
    await session.delete(await _get_source(session, source_id))
    await session.commit()


@router.post("/{source_id}/crawl", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl(source_id: int, session: SessionDep, kind: Literal["full", "live"] = "full") -> RunOut:
    await _get_source(session, source_id)
    if not await session.scalar(select(Rule.id).where(Rule.source_id == source_id, Rule.is_active)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该站点还没有启用的采集规则")
    running = await session.scalar(
        select(CrawlRun.id).where(CrawlRun.source_id == source_id, CrawlRun.status.in_(["pending", "running"]))
    )
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "该站点已有采集任务在执行")
    run = CrawlRun(source_id=source_id, trigger="manual", kind=kind, status="pending")
    session.add(run)
    await session.commit()
    await enqueue_crawl(run.id)
    return RunOut.model_validate(run)


@router.post("/{source_id}/logout", status_code=status.HTTP_204_NO_CONTENT)
async def clear_session(source_id: int, session: SessionDep) -> None:
    """清除已保存的登录会话，下次采集时重新登录。"""
    source = await _get_source(session, source_id)
    source.storage_state = None
    await session.commit()


@router.get("/{source_id}/schedules", response_model=list[ScheduleOut])
async def list_schedules(source_id: int, session: SessionDep) -> list[Schedule]:
    await _get_source(session, source_id)
    return list((await session.scalars(select(Schedule).where(Schedule.source_id == source_id).order_by(Schedule.kind))).all())


@router.put("/{source_id}/schedules", response_model=list[ScheduleOut])
async def save_schedules(source_id: int, body: list[ScheduleIn], session: SessionDep) -> list[Schedule]:
    """整体保存站点的定时任务（每种 kind 至多一个，未提交的 kind 会被删除）。"""
    from app.scheduler import next_fire, validate_cron

    await _get_source(session, source_id)
    if len({s.kind for s in body}) != len(body):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "同一类型的定时任务只能有一个")
    existing = {s.kind: s for s in (await session.scalars(select(Schedule).where(Schedule.source_id == source_id))).all()}
    for item in body:
        try:
            validate_cron(item.cron)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        sch = existing.pop(item.kind, None) or Schedule(source_id=source_id, kind=item.kind)
        if sch.cron != item.cron or sch.next_run_at is None or not sch.enabled:
            sch.next_run_at = next_fire(item.cron)
        sch.cron, sch.enabled = item.cron, item.enabled
        session.add(sch)
    for sch in existing.values():
        await session.delete(sch)
    await session.commit()
    return await list_schedules(source_id, session)


@router.get("/{source_id}/rules", response_model=list[RuleOut])
async def list_rules(source_id: int, session: SessionDep) -> list[Rule]:
    await _get_source(session, source_id)
    return list((await session.scalars(select(Rule).where(Rule.source_id == source_id).order_by(Rule.version.desc()))).all())


@router.post("/{source_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(source_id: int, body: RuleIn, session: SessionDep) -> Rule:
    await _get_source(session, source_id)
    rule = await _add_rule(session, source_id, body)
    await session.commit()
    return rule


@router.post("/{source_id}/rules/{rule_id}/activate", response_model=RuleOut)
async def activate_rule(source_id: int, rule_id: int, session: SessionDep) -> Rule:
    rule = await session.get(Rule, rule_id)
    if rule is None or rule.source_id != source_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "规则不存在")
    await session.execute(update(Rule).where(Rule.source_id == source_id).values(is_active=False))
    rule.is_active = True
    await session.commit()
    await session.refresh(rule)
    return rule


async def build_context(session: SessionDep, body: PreviewIn | Any, source: Source | None) -> FetchContext:
    """用表单中（可能未保存）的配置构造抓取上下文；编辑已有站点时复用其已保存的凭据与会话。"""
    stored, storage_state = None, None
    if source is not None and source.auth_type == body.auth_type:
        stored = decrypt_json(source.auth_config)
        storage_state = decrypt_json(source.storage_state)
    ctx = FetchContext(
        start_url=str(body.start_url), fetch_mode=body.fetch_mode, headers=body.headers,
        auth_type=body.auth_type, auth_config=merge_auth(body.auth_type, body.auth, stored),
        storage_state=storage_state,
    )
    return await apply_network(session, ctx, body.use_proxy)


@preview_router.post("/preview", response_model=PreviewOut)
async def preview(body: PreviewIn, session: SessionDep) -> PreviewOut:
    """按当前（未保存的）配置试跑，不写入数据库。"""
    source = await _get_source(session, body.source_id) if body.source_id else None
    ctx = await build_context(session, body, source)
    runlog = RunLog()
    try:
        result = await crawl(ctx, body.rule, rate_limit=2, max_pages=body.max_pages, runlog=runlog,
                             with_detail=body.rule.detail.enabled, max_entries=2)
    except LoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"抓取失败: {type(exc).__name__}: {exc}") from exc
    items = [{k: jsonable(v) for k, v in item.items()} for item in result.items[:100]]
    return PreviewOut(pages=result.pages, count=len(result.items), items=items, errors=result.errors[:20], log=runlog.entries)


@preview_router.post("/snapshot")
async def snapshot(body: SnapshotIn, session: SessionDep) -> dict:
    """抓取页面并返回清洗后的 HTML（去除脚本），供可视化选取器在 iframe 中展示。"""
    source = await _get_source(session, body.source_id) if body.source_id else None
    ctx = await build_context(session, body, source)
    browser = body.fetch_mode == "browser"
    try:
        async with open_fetcher(ctx, FetchConfig(wait_ms=1500 if browser else 0, scroll=browser, timeout=45)) as fetcher:
            fetched = await fetcher.fetch(ctx.start_url)
    except LoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"抓取失败: {type(exc).__name__}: {exc}") from exc
    return {"url": fetched.url, "html": sanitize_snapshot(fetched.html, fetched.url)}


@preview_router.post("/sniff")
async def sniff(body: SnapshotIn, session: SessionDep) -> dict:
    """用浏览器打开页面，找出返回拍品列表的 JSON 接口，并给出 api 模式规则草稿。"""
    from app.ai.rule_generator import capture_page
    from app.crawler.sniff import can_request_directly, draft_rule, find_candidates

    source = await _get_source(session, body.source_id) if body.source_id else None
    ctx = await build_context(session, body, source)
    try:
        snap = await capture_page(ctx, "browser", want_screenshot=False)
    except LoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"抓取失败: {type(exc).__name__}: {exc}") from exc
    candidates = find_candidates(snap.json_responses)
    out = []
    for c in candidates:
        direct = await can_request_directly(ctx, c)
        out.append({
            **{k: v for k, v in c.items() if k != "score"},
            "mode": "api" if direct else "capture",
            "rule": draft_rule(c, capture=not direct),
        })
    return {"captured": len(snap.json_responses), "candidates": out}
