import json
from typing import Any


def parse_cookies(value: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """支持 `a=b; c=d` 字符串，或浏览器插件导出的 JSON 数组（含 name/value/domain/path）。"""
    if not value:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            value = json.loads(text)
        else:
            cookies = []
            for part in text.split(";"):
                name, sep, val = part.strip().partition("=")
                if sep and name:
                    cookies.append({"name": name.strip(), "value": val.strip()})
            return cookies
    return [
        {"name": c["name"], "value": str(c.get("value", "")), "domain": c.get("domain"), "path": c.get("path") or "/"}
        for c in value
        if isinstance(c, dict) and c.get("name")
    ]
