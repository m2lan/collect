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
USER_AGENTS = [
    DEFAULT_UA,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
]


class LoginError(Exception):
    pass


class LoginExpired(LoginError):
    """Cookie / 人工登录的会话已失效，需要管理员重新登录（无法自动恢复）。"""


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
    # 代理地址，如 http://user:pass@1.2.3.4:8080 或 socks5://1.2.3.4:1080
    proxy: str | None = None
    user_agent: str = DEFAULT_UA

    @property
    def request_headers(self) -> dict[str, str]:
        has_ua = any(k.lower() == "user-agent" for k in self.headers)
        return self.headers if has_ua else {"User-Agent": self.user_agent, **self.headers}

    @property
    def uses_storage_state(self) -> bool:
        return self.auth_type in ("form", "session")

    def _logged_out(self, fetched: Fetched) -> bool:
        """跳转到了登录页，或页面上没有“已登录标志”元素。"""
        cfg = self.auth_config
        login_url = cfg.get("login_url") or ""
        if login_url and fetched.url.split("?")[0] == login_url.split("?")[0]:
            return True
        marker = cfg.get("logged_in_selector")
        if marker and fetched.html.lstrip()[:1] == "<":
            from parsel import Selector

            return not Selector(text=fetched.html).css(marker)
        return False

    def needs_login(self, fetched: Fetched) -> bool:
        """表单登录：会话失效时自动重新登录。"""
        return self.auth_type == "form" and self._logged_out(fetched)

    def check_session(self, fetched: Fetched) -> None:
        """Cookie / 人工登录：会话失效无法自动恢复，抛出 LoginExpired 以便提醒管理员。"""
        if self.auth_type in ("cookie", "session") and self._logged_out(fetched):
            how = "重新粘贴 Cookie" if self.auth_type == "cookie" else "在站点管理中使用“远程登录”重新登录"
            raise LoginExpired(f"登录已失效（{fetched.url}），请{how}")


class Fetcher(Protocol):
    async def fetch(self, url: str, method: str = "GET", body: dict[str, Any] | None = None) -> Fetched: ...


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
