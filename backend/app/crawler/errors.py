"""采集失败原因分类，用于看板统计。"""

import re

CATEGORIES = {
    "login": "登录失效",
    "blocked": "被拦截 / 风控",
    "timeout": "超时",
    "network": "网络 / 代理",
    "structure": "页面结构变化",
    "other": "其他",
}

_RULES = [
    ("login", r"登录已失效|登录失效|自动登录失败|登录配置|LoginError|LoginExpired"),
    ("blocked", r"HTTP 40[13]|HTTP 429|Cloudflare|Attention Required|captcha|验证码|人机|blocked|Access Denied|风控|频繁"),
    ("timeout", r"Timeout|timed out|超时"),
    ("network", r"Connect(ion)?Error|ConnectError|NameResolution|getaddrinfo|ProxyError|ERR_PROXY|ERR_CONNECTION|ERR_NAME|net::ERR|ReadError|RemoteProtocolError"),
    ("structure", r"未抽取到任何拍品|没有截获到|item_selector|规则"),
]


def classify_error(error: str | None) -> str | None:
    if not error:
        return None
    for category, pattern in _RULES:
        if re.search(pattern, error, re.I):
            return category
    return "other"
