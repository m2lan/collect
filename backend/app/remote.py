"""远程浏览器：管理员在页面上通过截图 + 鼠标键盘操作服务器上的浏览器，完成扫码 / 验证码 / 短信登录
或 Cloudflare 等人机验证，然后把浏览器状态保存为站点的登录会话。"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.crawler.fetchers import FetchContext
from app.crawler.fetchers.browser import _launch, _new_context

log = logging.getLogger(__name__)

VIEWPORT = {"width": 1280, "height": 800}
MAX_SESSIONS = 3
IDLE_SECONDS = 600


@dataclass
class RemoteSession:
    id: str
    source_id: int
    user_agent: str
    pw: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    last_active: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_active = time.monotonic()


class RemoteBrowserManager:
    def __init__(self) -> None:
        self.sessions: dict[str, RemoteSession] = {}
        self._lock = asyncio.Lock()

    async def start(self, source_id: int, ctx: FetchContext, url: str) -> RemoteSession:
        async with self._lock:
            await self._reap()
            for s in list(self.sessions.values()):
                if s.source_id == source_id:
                    await self._close(s)
            if len(self.sessions) >= MAX_SESSIONS:
                raise RuntimeError(f"同时最多 {MAX_SESSIONS} 个远程浏览器，请先关闭其他会话")
            pw = await async_playwright().start()
            try:
                browser = await _launch(pw, ctx.proxy)
                context = await _new_context(browser, ctx, viewport=VIEWPORT)
                page = await context.new_page()
                # 新打开的标签页（如扫码弹窗）自动切换为当前页
                session = RemoteSession(uuid.uuid4().hex, source_id, ctx.request_headers.get("User-Agent", ctx.user_agent),
                                        pw, browser, context, page)
                context.on("page", lambda p: setattr(session, "page", p))
                self.sessions[session.id] = session
            except Exception:
                await pw.stop()
                raise
        try:
            await page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        except Exception as exc:  # 页面加载失败也保留会话，管理员可在地址栏重试
            log.info("remote browser goto %s failed: %s", url, exc)
        return session

    def get(self, session_id: str) -> RemoteSession | None:
        s = self.sessions.get(session_id)
        if s:
            s.touch()
        return s

    async def screenshot(self, s: RemoteSession) -> bytes:
        return await s.page.screenshot(type="jpeg", quality=60)

    async def act(self, s: RemoteSession, action: dict[str, Any]) -> None:
        page, kind = s.page, action.get("type")
        if kind == "click":
            await page.mouse.click(float(action["x"]), float(action["y"]))
        elif kind == "dblclick":
            await page.mouse.dblclick(float(action["x"]), float(action["y"]))
        elif kind == "move":
            await page.mouse.move(float(action["x"]), float(action["y"]))
        elif kind == "down":
            await page.mouse.move(float(action["x"]), float(action["y"]))
            await page.mouse.down()
        elif kind == "up":
            await page.mouse.move(float(action["x"]), float(action["y"]))
            await page.mouse.up()
        elif kind == "scroll":
            await page.mouse.wheel(float(action.get("dx", 0)), float(action.get("dy", 0)))
        elif kind == "type":
            await page.keyboard.type(str(action.get("text", "")), delay=30)
        elif kind == "key":
            await page.keyboard.press(str(action["key"]))
        elif kind == "navigate":
            await page.goto(str(action["url"]), timeout=60_000, wait_until="domcontentloaded")
        elif kind == "back":
            await page.go_back(timeout=30_000)
        elif kind == "reload":
            await page.reload(timeout=60_000)
        else:
            raise ValueError(f"不支持的操作：{kind}")

    async def state(self, s: RemoteSession) -> dict[str, Any]:
        try:
            title = await s.page.title()
        except Exception:
            title = ""
        return {"url": s.page.url, "title": title, "viewport": VIEWPORT}

    async def storage_state(self, s: RemoteSession) -> dict[str, Any]:
        return await s.context.storage_state()  # type: ignore[return-value]

    async def close(self, session_id: str) -> None:
        async with self._lock:
            s = self.sessions.get(session_id)
            if s:
                await self._close(s)

    async def close_all(self) -> None:
        for s in list(self.sessions.values()):
            await self._close(s)

    async def _close(self, s: RemoteSession) -> None:
        self.sessions.pop(s.id, None)
        for closer in (s.browser.close, s.pw.stop):
            try:
                await closer()
            except Exception:
                pass

    async def _reap(self) -> None:
        now = time.monotonic()
        for s in list(self.sessions.values()):
            if now - s.last_active > IDLE_SECONDS:
                await self._close(s)


manager = RemoteBrowserManager()
