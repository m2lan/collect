import inspect
import json
from urllib.parse import unquote, urlsplit
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Response, async_playwright

from app.config import get_settings
from app.crawler.fetchers.base import FetchContext, Fetched, LoginError
from app.schemas.rule import FetchConfig


def _proxy_settings(proxy: str | None) -> dict[str, str] | None:
    if not proxy:
        return None
    u = urlsplit(proxy)
    server = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    out = {"server": server}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


async def _launch(pw: Playwright, proxy: str | None = None) -> Browser:
    settings = get_settings()
    return await pw.chromium.launch(
        headless=settings.browser_headless,
        executable_path=settings.browser_executable_path or None,
        args=["--disable-blink-features=AutomationControlled"],
        proxy=_proxy_settings(proxy),  # type: ignore[arg-type]
    )


async def _new_context(browser: Browser, ctx: FetchContext) -> BrowserContext:
    context = await browser.new_context(
        user_agent=ctx.request_headers.get("User-Agent", ctx.user_agent),
        extra_http_headers={k: v for k, v in ctx.headers.items() if k.lower() != "user-agent"},
        storage_state=ctx.storage_state if ctx.auth_type == "form" else None,
        locale="zh-CN",
    )
    if ctx.auth_type == "cookie":
        cookies = []
        for c in ctx.auth_config.get("cookies", []):
            cookie = {"name": c["name"], "value": c["value"], "path": c.get("path") or "/"}
            if c.get("domain"):
                cookie["domain"] = c["domain"]
            else:
                cookie["url"] = ctx.start_url
            cookies.append(cookie)
        if cookies:
            await context.add_cookies(cookies)
    return context


async def _do_login(page: Page, ctx: FetchContext, timeout_ms: int) -> dict[str, Any]:
    cfg = ctx.auth_config
    try:
        await page.goto(cfg["login_url"], timeout=timeout_ms, wait_until="domcontentloaded")
        await page.fill(cfg["username_selector"], cfg["username"], timeout=timeout_ms)
        await page.fill(cfg["password_selector"], cfg["password"], timeout=timeout_ms)
        await page.click(cfg["submit_selector"], timeout=timeout_ms)
        if cfg.get("logged_in_selector"):
            await page.wait_for_selector(cfg["logged_in_selector"], timeout=timeout_ms)
        else:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except KeyError as exc:
        raise LoginError(f"表单登录配置缺少 {exc.args[0]}") from exc
    except Exception as exc:
        raise LoginError(f"自动登录失败: {exc}") from exc
    state = await page.context.storage_state()
    ctx.storage_state = state  # type: ignore[assignment]
    if ctx.on_storage_state:
        result = ctx.on_storage_state(state)  # type: ignore[arg-type]
        if inspect.isawaitable(result):
            await result
    return state  # type: ignore[return-value]


async def login_with_browser(ctx: FetchContext, fetch: FetchConfig) -> dict[str, Any]:
    async with async_playwright() as pw:
        browser = await _launch(pw, ctx.proxy)
        try:
            context = await _new_context(browser, ctx)
            return await _do_login(await context.new_page(), ctx, fetch.timeout * 1000)
        finally:
            await browser.close()


class BrowserFetcher:
    def __init__(self, ctx: FetchContext, fetch: FetchConfig, capture_json: bool = False):
        self.ctx = ctx
        self.fetch_config = fetch
        self.capture_json = capture_json
        self._json: list[dict[str, Any]] = []
        self._pw_cm = async_playwright()
        self.browser: Browser | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserFetcher":
        pw = await self._pw_cm.__aenter__()
        self.browser = await _launch(pw, self.ctx.proxy)
        context = await _new_context(self.browser, self.ctx)
        self.page = await context.new_page()
        if self.capture_json:
            self.page.on("response", self._on_response)
        if self.ctx.auth_type == "form" and not self.ctx.storage_state:
            await _do_login(self.page, self.ctx, self.fetch_config.timeout * 1000)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.browser:
            await self.browser.close()
        await self._pw_cm.__aexit__(None, None, None)

    async def _on_response(self, resp: Response) -> None:
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            body = await resp.body()
            if len(body) > 2_000_000:
                return
            data = json.loads(body)
        except Exception:
            return
        self._json.append({
            "url": resp.url, "method": resp.request.method, "body": resp.request.post_data, "data": data,
        })

    async def screenshot(self) -> bytes | None:
        if not self.page:
            return None
        return await self.page.screenshot(full_page=False, type="png")

    async def _load(self, url: str) -> Fetched:
        assert self.page
        self._json = []
        cfg = self.fetch_config
        timeout = cfg.timeout * 1000
        resp = await self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        if cfg.wait_for:
            try:
                await self.page.wait_for_selector(cfg.wait_for, timeout=timeout)
            except Exception:
                pass  # 未出现时仍返回页面，由抽取结果体现问题
        if cfg.scroll:
            for _ in range(20):
                height = await self.page.evaluate("document.body.scrollHeight")
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.page.wait_for_timeout(500)
                if await self.page.evaluate("document.body.scrollHeight") == height:
                    break
        if cfg.wait_ms:
            await self.page.wait_for_timeout(cfg.wait_ms)
        return Fetched(
            url=self.page.url, status=resp.status if resp else 0, html=await self.page.content(),
            json_responses=list(self._json),
        )

    async def click_next(self, selector: str) -> Fetched | None:
        """点击"下一页"按钮（适用于没有真实链接、由 JS 翻页的网站），返回翻页后的页面；按钮不存在或不可用时返回 None。"""
        assert self.page
        cfg = self.fetch_config
        timeout = cfg.timeout * 1000
        button = self.page.locator(selector).first
        try:
            if not await button.count() or not await button.is_visible():
                return None
            cls = (await button.get_attribute("class") or "").lower()
            if await button.is_disabled() or "disabled" in cls.split() or "disable" in cls.split() or await button.get_attribute("aria-disabled") == "true":
                return None
            self._json = []
            await button.scroll_into_view_if_needed(timeout=timeout)
            await button.click(timeout=timeout)
        except Exception:
            return None
        try:
            await self.page.wait_for_load_state("networkidle", timeout=min(timeout, 15_000))
        except Exception:
            pass  # 长连接页面可能永远不会 networkidle
        if cfg.wait_for:
            try:
                await self.page.wait_for_selector(cfg.wait_for, timeout=timeout)
            except Exception:
                pass
        await self.page.wait_for_timeout(cfg.wait_ms or 800)
        return Fetched(url=self.page.url, status=200, html=await self.page.content(), json_responses=list(self._json))

    async def fetch(self, url: str, method: str = "GET", body: dict[str, Any] | None = None) -> Fetched:
        fetched = await self._load(url)
        if self.ctx.needs_login(fetched):
            assert self.page
            await _do_login(self.page, self.ctx, self.fetch_config.timeout * 1000)
            fetched = await self._load(url)
        if fetched.status >= 400:
            raise RuntimeError(f"HTTP {fetched.status}: {url}")
        return fetched
