"""浏览器实例池：同一进程内复用已启动的 Chromium（按代理区分），每次采集只新建隔离的 context，
省去每次启动浏览器的 1~2 秒和内存开销。"""

import asyncio
import logging
from collections import OrderedDict

from playwright.async_api import Browser, Playwright, async_playwright

from app.config import get_settings

log = logging.getLogger(__name__)
MAX_BROWSERS = 4


class BrowserPool:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._browsers: OrderedDict[str, Browser] = OrderedDict()
        self._lock: asyncio.Lock | None = None

    async def acquire(self, proxy: str | None, launcher) -> Browser:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:  # Playwright 对象绑定事件循环（测试中每个用例一个循环）
            self._pw, self._loop, self._browsers, self._lock = None, loop, OrderedDict(), asyncio.Lock()
        assert self._lock
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
            key = proxy or ""
            browser = self._browsers.get(key)
            if browser is not None and browser.is_connected():
                self._browsers.move_to_end(key)
                return browser
            browser = await launcher(self._pw, proxy)
            self._browsers[key] = browser
            while len(self._browsers) > MAX_BROWSERS:
                _, old = self._browsers.popitem(last=False)
                await old.close()
            return browser

    async def close(self) -> None:
        for b in self._browsers.values():
            try:
                await b.close()
            except Exception:
                pass
        self._browsers.clear()
        if self._pw:
            await self._pw.stop()
            self._pw = None

    @property
    def enabled(self) -> bool:
        return get_settings().browser_pool


pool = BrowserPool()
