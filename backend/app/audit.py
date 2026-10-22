"""操作日志中间件：记录 /api 下所有写操作（及登录），读操作和远程浏览器的鼠标键盘事件不记录。"""

import logging
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db import SessionLocal
from app.models import AuditLog
from app.security import decode_token

log = logging.getLogger(__name__)

SKIP = [re.compile(p) for p in (r"^/api/remote/[^/]+/action$", r"^/api/auth/login$")]

ACTIONS: list[tuple[str, str, str]] = [
    ("POST", r"^/api/sources$", "新建站点"),
    ("PUT", r"^/api/sources/\d+$", "编辑站点"),
    ("DELETE", r"^/api/sources/\d+$", "删除站点"),
    ("POST", r"^/api/sources/\d+/crawl$", "触发采集"),
    ("POST", r"^/api/sources/\d+/logout$", "清除登录会话"),
    ("PUT", r"^/api/sources/\d+/schedules$", "保存定时任务"),
    ("POST", r"^/api/sources/\d+/rules$", "新建规则版本"),
    ("POST", r"^/api/sources/\d+/rules/\d+/activate$", "切换规则版本"),
    ("POST", r"^/api/(preview|snapshot|sniff)$", "试跑 / 快照 / 嗅探"),
    ("POST", r"^/api/remote/start$", "打开远程浏览器"),
    ("POST", r"^/api/remote/[^/]+/save$", "保存远程登录会话"),
    ("POST", r"^/api/ai/generate$", "AI 生成规则"),
    ("POST", r"^/api/ai/tag$", "AI 批量标注"),
    ("POST", r"^/api/ai/ask$", "AI 问数"),
    ("PUT", r"^/api/ai/settings$", "修改 AI 设置"),
    ("POST|PUT|DELETE", r"^/api/ai/providers", "管理 AI 模型"),
    ("PUT", r"^/api/settings/", "修改系统设置"),
    ("POST", r"^/api/settings/alert/test$", "测试告警通知"),
    ("POST|PUT|DELETE", r"^/api/templates", "管理规则模板"),
    ("POST|PUT|DELETE", r"^/api/subscriptions", "管理订阅"),
    ("POST", r"^/api/backups$", "立即备份"),
    ("POST", r"^/api/analytics/report$", "生成行情报告"),
    ("POST|PUT|DELETE", r"^/api/users", "管理用户"),
    ("PUT", r"^/api/auth/password$", "修改密码"),
]


def action_for(method: str, path: str) -> str:
    for methods, pattern, label in ACTIONS:
        if method in methods.split("|") and re.search(pattern, path):
            return label
    return f"{method} {path}"


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)


async def write_audit(username: str | None, user_id: int | None, action: str, request: Request, status_code: int,
                      detail: str | None = None) -> None:
    try:
        async with SessionLocal() as session:
            session.add(AuditLog(
                user_id=user_id, username=username, action=action, method=request.method, path=request.url.path[:256],
                status_code=status_code, ip=client_ip(request), detail=detail,
            ))
            await session.commit()
    except Exception:
        log.exception("write audit log failed")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path
        if request.method in ("GET", "HEAD", "OPTIONS") or not path.startswith("/api") or any(p.search(path) for p in SKIP):
            return response
        auth = request.headers.get("authorization", "")
        username = decode_token(auth[7:]) if auth.lower().startswith("bearer ") else None
        if username is None and response.status_code in (401, 403):
            return response  # 未登录的请求不记录
        await write_audit(username, None, action_for(request.method, path), request, response.status_code)
        return response
