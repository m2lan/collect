from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.rule import FetchConfig

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class LoginError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    status: int
    html: str
    # 浏览器模式下捕获的 JSON 接口响应：[{"url", "method", "data"}]
    json_responses: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FetchContext:
    """一次抓取所需的站点信息（凭据已解密）。"""

    start_url: str
    fetch_mode: str = "static"
    headers: dict[str, str] = field(default_factory=dict)
    auth_type: str = "none"
    auth_config: dict[str, Any] = field(default_factory=dict)
    storage_state: dict[str, Any] | None = None
    # 表单登录成功后回调，用于持久化新的浏览器状态
    on_storage_state: Callable[[dict[str, Any]], Any] | None = None

    def needs_login(self, fetched: Fetched) -> bool:
        if self.auth_type != "form":
            return False
        cfg = self.auth_config
        login_url = cfg.get("login_url") or ""
        if login_url and fetched.url.split("?")[0] == login_url.split("?")[0]:
            return True
        marker = cfg.get("logged_in_selector")
        if marker:
            from parsel import Selector

            return not Selector(text=fetched.html).css(marker)
        return False


class Fetcher(Protocol):
    async def fetch(self, url: str) -> Fetched: ...


@asynccontextmanager
async def open_fetcher(ctx: FetchContext, fetch: FetchConfig, capture_json: bool = False) -> AsyncIterator[Fetcher]:
    mode = fetch.mode or ctx.fetch_mode
    if mode == "browser":
        from app.crawler.fetchers.browser import BrowserFetcher

        async with BrowserFetcher(ctx, fetch, capture_json=capture_json) as f:
            yield f
    else:
        from app.crawler.fetchers.static import StaticFetcher

        async with StaticFetcher(ctx, fetch) as f:
            yield f
