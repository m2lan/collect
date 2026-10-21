from typing import Any

import httpx

from app.crawler.fetchers.base import DEFAULT_UA, FetchContext, Fetched
from app.schemas.rule import FetchConfig


def _cookie_jar(cookies: list[dict[str, Any]]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for c in cookies:
        jar.set(c["name"], c["value"], domain=(c.get("domain") or "").lstrip("."), path=c.get("path") or "/")
    return jar


class StaticFetcher:
    def __init__(self, ctx: FetchContext, fetch: FetchConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.ctx = ctx
        self.fetch_config = fetch
        self.transport = transport
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "StaticFetcher":
        if self.ctx.auth_type == "form" and not self.ctx.storage_state:
            await self._login()
        self.client = httpx.AsyncClient(
            headers={"User-Agent": DEFAULT_UA, **self.ctx.headers},
            cookies=self._cookies(),
            timeout=self.fetch_config.timeout,
            follow_redirects=True,
            transport=self.transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.client:
            await self.client.aclose()

    def _cookies(self) -> httpx.Cookies:
        if self.ctx.auth_type == "cookie":
            return _cookie_jar(self.ctx.auth_config.get("cookies", []))
        if self.ctx.storage_state:
            return _cookie_jar(self.ctx.storage_state.get("cookies", []))
        return httpx.Cookies()

    async def _login(self) -> None:
        # 表单登录统一交给浏览器完成，再复用其 Cookie
        from app.crawler.fetchers.browser import login_with_browser

        await login_with_browser(self.ctx, self.fetch_config)

    async def fetch(self, url: str) -> Fetched:
        assert self.client
        resp = await self.client.get(url)
        fetched = Fetched(url=str(resp.url), status=resp.status_code, html=resp.text)
        if self.ctx.needs_login(fetched):
            await self._login()
            self.client.cookies = self._cookies()
            resp = await self.client.get(url)
            fetched = Fetched(url=str(resp.url), status=resp.status_code, html=resp.text)
        resp.raise_for_status()
        return fetched
