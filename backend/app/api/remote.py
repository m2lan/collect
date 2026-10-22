from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.crawler.runner import context_for, network_options
from app.models import Source
from app.models.entities import utcnow
from app.remote import manager
from app.security import decrypt_json, encrypt_json

router = APIRouter(prefix="/remote", tags=["remote"])


class StartIn(BaseModel):
    source_id: int
    url: str | None = None


class ActionIn(BaseModel):
    type: str
    x: float | None = None
    y: float | None = None
    dx: float | None = None
    dy: float | None = None
    text: str | None = None
    key: str | None = None
    url: str | None = None


def _session(session_id: str):
    s = manager.get(session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "远程浏览器会话不存在或已超时关闭")
    return s


@router.post("/start")
async def start(body: StartIn, session: SessionDep) -> dict[str, Any]:
    source = await session.get(Source, body.source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
    ctx = context_for(source)
    ctx.proxy, ctx.user_agent = await network_options(session, source.use_proxy)
    if ctx.auth_config.get("user_agent"):
        ctx.user_agent = ctx.auth_config["user_agent"]
    # 在已有会话基础上继续（例如补充二次验证），表单登录的会话同样适用
    if ctx.storage_state and ctx.auth_type in ("none", "cookie"):
        ctx.auth_type = "session"
    try:
        s = await manager.start(source.id, ctx, body.url or ctx.auth_config.get("login_url") or source.start_url)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    return {"session_id": s.id, **await manager.state(s)}


@router.get("/{session_id}/screenshot")
async def screenshot(session_id: str) -> Response:
    s = _session(session_id)
    try:
        data = await manager.screenshot(s)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"截图失败：{exc}") from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{session_id}/state")
async def state(session_id: str) -> dict[str, Any]:
    return await manager.state(_session(session_id))


@router.post("/{session_id}/action")
async def action(session_id: str, body: ActionIn) -> dict[str, Any]:
    s = _session(session_id)
    try:
        await manager.act(s, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"操作失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
    return await manager.state(s)


@router.post("/{session_id}/save")
async def save(session_id: str, session: SessionDep) -> dict[str, Any]:
    """保存当前浏览器状态为站点的登录会话（Cookie + localStorage），站点登录方式切换为“人工登录”。"""
    s = _session(session_id)
    source = await session.get(Source, s.source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
    state = await manager.storage_state(s)
    cfg = decrypt_json(source.auth_config) or {}
    if source.auth_type in ("none", "cookie"):
        markers = {k: cfg.get(k) for k in ("login_url", "logged_in_selector")}
        cfg, source.auth_type = markers, "session"
    cfg["user_agent"] = s.user_agent
    cfg["saved_at"] = utcnow().isoformat(timespec="seconds")
    source.auth_config = encrypt_json(cfg)
    source.storage_state = encrypt_json(state)
    source.login_expired_at = None
    await session.commit()
    return {"ok": True, "cookies": len(state.get("cookies", [])), "auth_type": source.auth_type}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close(session_id: str) -> None:
    await manager.close(session_id)
