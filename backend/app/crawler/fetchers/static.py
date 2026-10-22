from typing import Any

import httpx

from app.crawler.fetchers.base import FetchContext, Fetched
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
            headers=self.ctx.request_headers,
            proxy=self.ctx.proxy or None,
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
        if self.ctx.uses_storage_state and self.ctx.storage_state:
            return _cookie_jar(self.ctx.storage_state.get("cookies", []))
        return httpx.Cookies()

    async def _login(self) -> None:
        # 表单登录统一交给浏览器完成，再复用其 Cookie
        from app.crawler.fetchers.browser import login_with_browser

        await login_with_browser(self.ctx, self.fetch_config)

    async def _request(self, url: str, method: str, body: dict[str, Any] | None) -> httpx.Response:
        assert self.client
        if method == "POST":
            return await self.client.post(url, json=body or {})
        return await self.client.get(url)

    async def fetch(self, url: str, method: str = "GET", body: dict[str, Any] | None = None) -> Fetched:
        resp = await self._request(url, method, body)
        fetched = Fetched(url=str(resp.url), status=resp.status_code, html=resp.text)
        if self.ctx.needs_login(fetched):
            await self._login()
            assert self.client
            self.client.cookies = self._cookies()
            resp = await self._request(url, method, body)
            fetched = Fetched(url=str(resp.url), status=resp.status_code, html=resp.text)
        self.ctx.check_session(fetched)
        resp.raise_for_status()
        return fetched
