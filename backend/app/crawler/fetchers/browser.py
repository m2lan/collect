import inspect
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings
from app.crawler.fetchers.base import DEFAULT_UA, FetchContext, Fetched, LoginError
from app.schemas.rule import FetchConfig


async def _launch(pw: Playwright) -> Browser:
    settings = get_settings()
    return await pw.chromium.launch(
        headless=settings.browser_headless,
        executable_path=settings.browser_executable_path or None,
        args=["--disable-blink-features=AutomationControlled"],
    )


async def _new_context(browser: Browser, ctx: FetchContext) -> BrowserContext:
    context = await browser.new_context(
        user_agent=ctx.headers.get("User-Agent", DEFAULT_UA),
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
        browser = await _launch(pw)
        try:
            context = await _new_context(browser, ctx)
            return await _do_login(await context.new_page(), ctx, fetch.timeout * 1000)
        finally:
            await browser.close()


class BrowserFetcher:
    def __init__(self, ctx: FetchContext, fetch: FetchConfig):
        self.ctx = ctx
        self.fetch_config = fetch
        self._pw_cm = async_playwright()
        self.browser: Browser | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserFetcher":
        pw = await self._pw_cm.__aenter__()
        self.browser = await _launch(pw)
        context = await _new_context(self.browser, self.ctx)
        self.page = await context.new_page()
        if self.ctx.auth_type == "form" and not self.ctx.storage_state:
            await _do_login(self.page, self.ctx, self.fetch_config.timeout * 1000)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.browser:
            await self.browser.close()
        await self._pw_cm.__aexit__(None, None, None)

    async def _load(self, url: str) -> Fetched:
        assert self.page
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
        return Fetched(url=self.page.url, status=resp.status if resp else 0, html=await self.page.content())

    async def fetch(self, url: str) -> Fetched:
        fetched = await self._load(url)
        if self.ctx.needs_login(fetched):
            assert self.page
            await _do_login(self.page, self.ctx, self.fetch_config.timeout * 1000)
            fetched = await self._load(url)
        if fetched.status >= 400:
            raise RuntimeError(f"HTTP {fetched.status}: {url}")
        return fetched
