import json

import pytest

from app.ai import service
from app.ai.providers import LLMResult, ProviderConfig
from tests.conftest import RULE
from tests.test_api import HAS_BROWSER, client, server, wait_run  # noqa: F401
from tests.test_m4 import dyn  # noqa: F401

CLICK_RULE = {
    "fetch": {"mode": "browser", "wait_for": ".lot"},
    "list": {
        "item_selector": ".lot", "unique_key": "url",
        "fields": [
            {"key": "title", "selector": ".name", "transform": ["trim"]},
            {"key": "url", "selector": ".name", "attr": "href", "transform": ["absolute_url"]},
            {"key": "current_price", "selector": ".cur", "transform": ["price"]},
        ],
    },
    "pagination": {"type": "click", "selector": "button.next-btn", "max_pages": 5},
}
DEPOSIT_REGEX = {"key": "deposit", "type": "regex", "pattern": "保证金[：:\\s]*([¥￥]?[\\d,.]+)", "transform": ["price"]}


class ScriptedProvider:
    """按工具名分别返回预设结果，并记录每次请求的消息。"""

    def __init__(self, scripts: dict[str, list[dict]]):
        self.config = ProviderConfig(protocol="openai", model="m", api_key="k")
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[tuple[str, list]] = []

    async def generate_structured(self, system, messages, schema, tool_name, tool_description):
        self.calls.append((tool_name, list(messages)))
        return LLMResult(self.scripts[tool_name].pop(0))


async def enable_ai(client, monkeypatch, provider):
    monkeypatch.setattr(service, "provider_factory", lambda cfg: provider)
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 0, "auto_repair": False, "max_rounds": 3})
    await client.post("/api/ai/providers", json={"name": "c", "protocol": "openai", "api_key": "sk-1234567890ab", "model": "m"})


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_click_pagination(client, dyn):
    _, base = dyn
    r = (await client.post("/api/preview", json={"start_url": f"{base}/jslist", "fetch_mode": "browser", "rule": CLICK_RULE, "max_pages": 5})).json()
    # 3 个拍品分 2 页；第 2 页后按钮被禁用，停止翻页
    assert r["count"] == 3 and r["pages"] == 2, r
    assert {it["title"] for it in r["items"]} == {"清乾隆 青花缠枝莲纹瓶", "民国 粉彩人物盘", "宋 建窑兔毫盏"}


def test_click_pagination_rejects_static_mode():
    from app.schemas.rule import RuleConfig

    with pytest.raises(ValueError, match="浏览器"):
        RuleConfig.model_validate({**CLICK_RULE, "fetch": {"mode": "static"}})


async def test_ai_fixes_broken_pagination(client, server, monkeypatch):
    broken = {**RULE, "pagination": {"type": "next_link", "selector": "a.no-such-next", "max_pages": 5}}
    provider = ScriptedProvider({"submit_rule": [{"explanation": "v1", "rule": broken}, {"explanation": "v2", "rule": RULE}]})
    await enable_ai(client, monkeypatch, provider)
    out = (await client.post("/api/ai/generate", json={"start_url": f"{server}/list", "fetch_mode": "static"})).json()
    assert out["ok"] and out["rounds"] == 2 and out["rule"]["pagination"]["selector"] == "a.next"
    repair = provider.calls[1][1][-1].content
    assert "翻页没有生效" in repair


async def test_ai_pagination_falls_back_to_first_page(client, server, monkeypatch):
    broken = {**RULE, "pagination": {"type": "next_link", "selector": "a.no-such-next", "max_pages": 5}}
    provider = ScriptedProvider({"submit_rule": [{"explanation": "x", "rule": broken}] * 3})
    await enable_ai(client, monkeypatch, provider)
    out = (await client.post("/api/ai/generate", json={"start_url": f"{server}/list", "fetch_mode": "static"})).json()
    assert out["ok"] and out["rule"]["pagination"]["type"] == "none"
    assert any("已设为只抓第一页" in p for p in out["problems"])


async def test_ai_detail_stage_for_missing_fields(client, dyn, monkeypatch):
    _, base = dyn
    rule = {**RULE, "pagination": {"type": "none"}}
    provider = ScriptedProvider({
        "submit_rule": [{"explanation": "列表页", "rule": rule, "missing_fields": ["deposit", "estimate"]}],
        "submit_detail_rule": [
            {"explanation": "猜错了", "fields": [{"key": "deposit", "selector": ".nope", "transform": ["price"]}]},
            {"explanation": "改用正则", "fields": [
                DEPOSIT_REGEX,
                {"key": "estimate", "selector": ".estimate", "transform": [{"name": "regex_replace", "pattern": "^估价：", "repl": ""}]},
            ]},
        ],
    })
    await enable_ai(client, monkeypatch, provider)
    src = (await client.post("/api/sources", json={"name": "保证金站", "start_url": f"{base}/list", "rate_limit": 20})).json()
    out = (await client.post("/api/ai/generate", json={
        "source_id": src["id"], "start_url": f"{base}/list", "fetch_mode": "static", "hint": "还要抓保证金和估价", "apply": True,
    })).json()
    assert out["ok"] and out["missing_fields"] == ["deposit", "estimate"], out
    detail = out["rule"]["detail"]
    assert detail["enabled"] and [f["key"] for f in detail["fields"]] == ["deposit", "estimate"]
    assert out["items"][0]["deposit"] == "5000" and "【详情页】改用正则" in out["explanation"]
    # 详情阶段的提示里带有详情页内容和管理员要求
    detail_msg = next(m for name, msgs in provider.calls if name == "submit_detail_rule" for m in msgs[:1])
    assert "保证金 ¥5,000" in detail_msg.content and "还要抓保证金" in detail_msg.content

    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "success"
    lots = (await client.get("/api/lots", params={"source_id": src["id"]})).json()["items"]
    assert all(lot["extra"].get("deposit") == "5000" for lot in lots), [lot["extra"] for lot in lots]


async def test_ai_detail_stage_forced_by_option(client, dyn, monkeypatch):
    _, base = dyn
    provider = ScriptedProvider({
        "submit_rule": [{"explanation": "列表页", "rule": {**RULE, "pagination": {"type": "none"}}}],
        "submit_detail_rule": [{"explanation": "详情", "fields": [DEPOSIT_REGEX]}],
    })
    await enable_ai(client, monkeypatch, provider)
    out = (await client.post("/api/ai/generate", json={"start_url": f"{base}/list", "fetch_mode": "static", "analyze_detail": True})).json()
    assert out["rule"]["detail"]["enabled"] and out["items"][0]["deposit"] == "5000"
    # 未要求详情时不进入详情阶段
    provider2 = ScriptedProvider({"submit_rule": [{"explanation": "列表页", "rule": {**RULE, "pagination": {"type": "none"}}}]})
    monkeypatch.setattr(service, "provider_factory", lambda cfg: provider2)
    out = (await client.post("/api/ai/generate", json={"start_url": f"{base}/list", "fetch_mode": "static"})).json()
    assert not out["rule"]["detail"]["enabled"] and [c[0] for c in provider2.calls] == ["submit_rule"]
