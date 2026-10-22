import asyncio
import time

import pytest

from app.crawler.fetchers import FetchContext
from app.crawler.fetchers.browser import BrowserFetcher, _launch
from app.crawler.fetchers.pool import BrowserPool
from app.crawler.throttle import domain_of, throttle
from app.schemas.rule import FetchConfig
from tests.test_api import HAS_BROWSER, server  # noqa: F401


def test_domain_of():
    assert domain_of("https://zc-paimai.taobao.com/x") == "taobao.com"
    assert domain_of("https://h5api.m.taobao.com/h5/") == "taobao.com"
    assert domain_of("https://www.example.com.cn/a") == "example.com.cn"
    assert domain_of("http://127.0.0.1:8765/list") == "127.0.0.1"


async def test_domain_throttle_spaces_requests_across_callers():
    started = time.monotonic()
    # 同一主域名的 3 个并发请求依次间隔 0.3s；不同域名不受影响
    waits = await asyncio.gather(*(throttle(f"https://a{i}.slow-site.com/", 0.3) for i in range(3)), throttle("https://other.com/", 0.3))
    assert sorted(round(w, 1) for w in waits[:3]) == [0.0, 0.3, 0.6] and waits[3] == 0
    assert time.monotonic() - started >= 0.55


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_stealth_hides_automation(server):
    ctx = FetchContext(start_url=f"{server}/list", fetch_mode="browser")
    async with BrowserFetcher(ctx, FetchConfig(mode="browser")) as f:
        await f.fetch(f"{server}/list")
        assert await f.page.evaluate("navigator.webdriver === undefined")
        assert await f.page.evaluate("navigator.plugins.length") >= 3
        assert await f.page.evaluate("!!window.chrome")
    ctx.stealth = False
    async with BrowserFetcher(ctx, FetchConfig(mode="browser")) as f:
        await f.fetch(f"{server}/list")
        # 未伪装时仍是 false（启动参数已去掉自动化标记），但属性存在
        assert await f.page.evaluate("navigator.webdriver === false")


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_browser_pool_reuses_browser():
    pool = BrowserPool()
    launches = []

    async def launcher(pw, proxy):
        launches.append(proxy)
        return await _launch(pw, proxy)

    b1 = await pool.acquire(None, launcher)
    b2 = await pool.acquire(None, launcher)
    assert b1 is b2 and launches == [None]
    await pool.close()
    assert not b1.is_connected()
