"""告警通知：支持企业微信、钉钉、飞书群机器人及通用 JSON Webhook。"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_store import get_setting

log = logging.getLogger(__name__)


def _payload(kind: str, title: str, text: str) -> dict:
    content = f"【拍卖采集】{title}\n{text}"
    if kind == "wecom":
        return {"msgtype": "text", "text": {"content": content}}
    if kind == "dingtalk":
        return {"msgtype": "text", "text": {"content": content}}
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": content}}
    return {"title": title, "text": text}


async def send_webhook(kind: str, url: str, title: str, text: str) -> tuple[bool, str]:
    if kind == "none" or not url:
        return False, "未配置告警通知"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=_payload(kind, title, text))
        ok = resp.status_code < 400
        return ok, f"HTTP {resp.status_code} {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def notify(session: AsyncSession, title: str, text: str) -> None:
    kind = await get_setting(session, "alert.webhook_type")
    url = await get_setting(session, "alert.webhook_url")
    if kind == "none" or not url:
        return
    ok, detail = await send_webhook(kind, url, title, text)
    if not ok:
        log.warning("alert webhook failed: %s", detail)
