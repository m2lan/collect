from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.api.deps import SessionDep
from app.crawler.cookies import parse_cookies
from app.crawler.fetchers import FetchContext, LoginError
from app.crawler.pipeline import jsonable
from app.crawler.runner import crawl
from app.models import CrawlRun, Lot, Rule, Source
from app.schemas.api import (
    AuthIn,
    AuthOut,
    PreviewIn,
    PreviewOut,
    RuleIn,
    RuleOut,
    RunOut,
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
    if auth_type == "cookie":
        cookies = parse_cookies(incoming.cookies) if incoming.cookies else stored.get("cookies", [])
        return {"cookies": cookies}
    if auth_type == "form":
        merged = {k: stored.get(k) for k in _FORM_KEYS}
        for k in _FORM_KEYS:
            v = getattr(incoming, k)
            if v:  # 空值保留原配置（编辑时密码不回显）
                merged[k] = v
        return merged
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
        enabled=source.enabled, created_at=source.created_at, auth=_auth_out(source),
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
        auth_config=encrypt_json(merge_auth(body.auth_type, body.auth, None)),
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
    stored = decrypt_json(source.auth_config) if body.auth_type == source.auth_type else None
    new_auth = merge_auth(body.auth_type, body.auth, stored)
    if new_auth != (decrypt_json(source.auth_config) or {}):
        source.storage_state = None  # 登录信息变化后旧会话失效
    source.auth_config = encrypt_json(new_auth)
    for field in ("name", "fetch_mode", "headers", "auth_type", "rate_limit", "enabled"):
        setattr(source, field, getattr(body, field))
    source.start_url = str(body.start_url)
    if body.rule:
        active = await session.scalar(select(Rule).where(Rule.source_id == source_id, Rule.is_active))
        if active is None or active.config != body.rule.model_dump(by_alias=True):
            await _add_rule(session, source_id, RuleIn(config=body.rule), body.rule_origin)
    await session.commit()
    return await _source_out(session, source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: int, session: SessionDep) -> None:
    await session.delete(await _get_source(session, source_id))
    await session.commit()


@router.post("/{source_id}/crawl", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl(source_id: int, session: SessionDep) -> RunOut:
    await _get_source(session, source_id)
    if not await session.scalar(select(Rule.id).where(Rule.source_id == source_id, Rule.is_active)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该站点还没有启用的采集规则")
    running = await session.scalar(
        select(CrawlRun.id).where(CrawlRun.source_id == source_id, CrawlRun.status.in_(["pending", "running"]))
    )
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "该站点已有采集任务在执行")
    run = CrawlRun(source_id=source_id, trigger="manual", status="pending")
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


def build_context(body: PreviewIn | Any, source: Source | None) -> FetchContext:
    """用表单中（可能未保存）的配置构造抓取上下文；编辑已有站点时复用其已保存的凭据与会话。"""
    stored, storage_state = None, None
    if source is not None and source.auth_type == body.auth_type:
        stored = decrypt_json(source.auth_config)
        storage_state = decrypt_json(source.storage_state)
    return FetchContext(
        start_url=str(body.start_url), fetch_mode=body.fetch_mode, headers=body.headers,
        auth_type=body.auth_type, auth_config=merge_auth(body.auth_type, body.auth, stored),
        storage_state=storage_state,
    )


@preview_router.post("/preview", response_model=PreviewOut)
async def preview(body: PreviewIn, session: SessionDep) -> PreviewOut:
    """按当前（未保存的）配置试跑，不写入数据库。"""
    source = await _get_source(session, body.source_id) if body.source_id else None
    ctx = build_context(body, source)
    try:
        result = await crawl(ctx, body.rule, rate_limit=2, max_pages=body.max_pages)
    except LoginError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"抓取失败: {type(exc).__name__}: {exc}") from exc
    items = [{k: jsonable(v) for k, v in item.items()} for item in result.items[:100]]
    return PreviewOut(pages=result.pages, count=len(result.items), items=items, errors=result.errors[:20])
