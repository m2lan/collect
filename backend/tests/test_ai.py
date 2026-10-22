import json

import httpx
import httpx2
import pytest

from app.ai import service
from app.ai.html_compress import compress_html
from app.ai.providers import LLMResult, Message, ProviderConfig, build_provider
from tests.conftest import RULE
from tests.test_api import client, server  # noqa: F401  复用 fixture

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _cfg(protocol: str, **kw) -> ProviderConfig:
    return ProviderConfig(protocol=protocol, model="m", api_key="sk-test", base_url="https://llm.test/v1", **kw)


async def test_openai_tool_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "report", "arguments": '{"ok": true}'}}],
            }}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        })

    p = build_provider(_cfg("openai"), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")
    assert r.data == {"ok": True} and (r.prompt_tokens, r.completion_tokens) == (11, 3)
    assert seen["tool_choice"]["function"]["name"] == "report"
    assert seen["messages"][0] == {"role": "system", "content": "sys"}
    assert "temperature" not in seen


async def test_openai_json_fallback_without_tools():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "tools" not in body and "JSON Schema" in body["messages"][0]["content"]
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "好的\n```json\n{\"ok\": true}\n```"}}],
        })

    p = build_provider(_cfg("openai", supports_tools=False, temperature=0), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")
    assert r.data == {"ok": True}


async def test_anthropic_tool_use():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        seen["url"] = str(request.url)
        return httpx2.Response(200, json={
            "id": "msg", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "t1", "name": "report", "input": {"ok": True}}],
            "usage": {"input_tokens": 20, "output_tokens": 5},
        })

    p = build_provider(_cfg("anthropic", supports_vision=True), httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    r = await p.generate_structured("sys", [Message("user", "hi", [b"\x89PNG"])], SCHEMA, "report", "d")
    assert r.data == {"ok": True} and r.prompt_tokens == 20
    assert seen["system"] == "sys" and seen["tool_choice"] == {"type": "tool", "name": "report"}
    assert seen["messages"][0]["content"][0]["type"] == "image"
    assert seen["url"].endswith("/v1/messages")


def test_compress_html_collapses_repeats():
    items = "".join(f'<div class="lot"><span class="p">{i}</span></div>' for i in range(20))
    out = compress_html(f"<html><body><script>x</script><div class='list'>{items}</div></body></html>")
    assert "<script" not in out and out.count('class="lot"') == 3 and "另有 17 个" in out


class FakeProvider:
    """按脚本依次返回规则的假模型。"""

    def __init__(self, responses: list[dict]):
        self.config = _cfg("openai")
        self.responses = responses
        self.calls: list[list[Message]] = []

    async def generate_structured(self, system, messages, schema, tool_name, tool_description):
        assert tool_name == "submit_rule" and "rule" in schema["properties"]
        self.calls.append(list(messages))
        return LLMResult(self.responses[len(self.calls) - 1], prompt_tokens=100, completion_tokens=50)


@pytest.fixture
def fake(monkeypatch):
    bad = {**RULE, "list": {**RULE["list"], "item_selector": ".does-not-exist"}}
    provider = FakeProvider([
        {"explanation": "第一次猜测", "rule": bad},
        {"explanation": "修正后的规则", "rule": RULE},
    ])
    monkeypatch.setattr(service, "provider_factory", lambda cfg: provider)
    return provider


async def _enable_ai(client):
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 2, "auto_repair": False, "max_rounds": 3})
    r = await client.post("/api/ai/providers", json={
        "name": "Claude", "protocol": "anthropic", "api_key": "sk-ant-1234567890", "model": "claude-x",
    })
    assert r.status_code == 201, r.text
    return r.json()


async def test_ai_disabled(client, server):
    r = await client.post("/api/ai/generate", json={"start_url": f"{server}/list", "fetch_mode": "static"})
    assert r.status_code == 403
    st = (await client.get("/api/ai/settings")).json()
    assert st["available"] is False and st["reason"] == "AI 功能未开启"
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 5, "auto_repair": False, "max_rounds": 3})
    st = (await client.get("/api/ai/settings")).json()
    assert st["available"] is False and "没有可用的 AI 模型" in st["reason"] and st["used_today"] == 0


async def test_ai_generate_self_repair_and_apply(client, server, fake):
    provider = await _enable_ai(client)
    assert provider["is_default"] and provider["api_key_masked"] == "sk-****7890"
    assert (await client.get("/api/ai/settings")).json()["available"] is True

    src = (await client.post("/api/sources", json={"name": "AI站", "start_url": f"{server}/list"})).json()
    r = await client.post("/api/ai/generate", json={
        "source_id": src["id"], "start_url": f"{server}/list", "fetch_mode": "static",
        "hint": "还要抓拍品编号", "apply": True,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["rounds"] == 2 and out["count"] == 2
    assert out["explanation"] == "修正后的规则" and out["rule_version"] == 1
    assert out["prompt_tokens"] == 200
    # 第二轮请求中带上了试跑发现的问题
    repair = fake.calls[1][-1].content
    assert ".does-not-exist" in repair
    assert "还要抓拍品编号" in fake.calls[0][0].content

    source = (await client.get(f"/api/sources/{src['id']}")).json()
    assert source["active_rule_version"] == 1
    rules = (await client.get(f"/api/sources/{src['id']}/rules")).json()
    assert rules[0]["origin"] == "ai"

    gens = (await client.get("/api/ai/generations")).json()
    assert gens["total"] == 1 and gens["items"][0]["status"] == "success"
    assert gens["items"][0]["result_rule_id"] == rules[0]["id"]


async def test_ai_daily_limit(client, server, monkeypatch):
    await _enable_ai(client)
    monkeypatch.setattr(service, "provider_factory", lambda cfg: FakeProvider([{"explanation": "", "rule": RULE}] * 3))
    body = {"start_url": f"{server}/list", "fetch_mode": "static"}
    for _ in range(2):
        assert (await client.post("/api/ai/generate", json=body)).status_code == 200
    assert (await client.post("/api/ai/generate", json=body)).status_code == 429


async def test_provider_test_endpoint(client, monkeypatch):
    p = await _enable_ai(client)

    class Echo:
        config = _cfg("anthropic")

        async def generate_structured(self, *a, **k):
            return LLMResult({"ok": True, "model": "claude-x"})

    monkeypatch.setattr(service, "provider_factory", lambda cfg: Echo())
    r = (await client.post(f"/api/ai/providers/{p['id']}/test")).json()
    assert r["ok"] is True
    # 编辑时不传 key 则保留原值
    upd = await client.put(f"/api/ai/providers/{p['id']}", json={"name": "C2", "protocol": "openai", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"})
    assert upd.json()["api_key_masked"] == "sk-****7890"


THINKING_ERROR = {"error": {"message": "Thinking mode does not support this tool_choice (request_id: x)",
                            "type": "invalid_request_error", "param": None, "code": "invalid_request_error"}}


async def test_openai_thinking_mode_falls_back_to_auto_tool_choice():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body["tool_choice"] != "auto":
            return httpx.Response(400, json=THINKING_ERROR)
        assert "必须调用工具 report" in body["messages"][0]["content"]
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "reasoning_content": "先想一想…",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "report", "arguments": '{"ok": true}'}}],
            }}],
        })

    cfg = ProviderConfig(protocol="openai", model="deepseek-reasoner", api_key="k", base_url="https://thinking.test/v1")
    p = build_provider(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")
    assert r.data == {"ok": True} and len(calls) == 2
    # 同一模型的后续请求（新的 provider 实例）直接使用 auto，不再先失败一次
    p2 = build_provider(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await p2.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")
    assert len(calls) == 3 and calls[2]["tool_choice"] == "auto"


async def test_openai_thinking_mode_text_answer_is_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["tool_choice"] != "auto":
            return httpx.Response(400, json=THINKING_ERROR)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '```json\n{"ok": true}\n```'}}],
        })

    cfg = ProviderConfig(protocol="openai", model="kimi-thinking", api_key="k", base_url="https://thinking2.test/v1")
    p = build_provider(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")).data == {"ok": True}


async def test_anthropic_thinking_falls_back_to_auto_tool_choice():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body["tool_choice"]["type"] == "tool":
            return httpx2.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "Thinking may not be enabled when tool_choice forces tool use."}})
        assert "必须调用工具 report" in body["system"]
        return httpx2.Response(200, json={
            "id": "msg", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "…", "signature": "s"},
                {"type": "tool_use", "id": "t1", "name": "report", "input": {"ok": True}},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    cfg = ProviderConfig(protocol="anthropic", model="claude-thinking", api_key="k", base_url="https://anthropic-thinking.test")
    p = build_provider(cfg, httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    r = await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")
    assert r.data == {"ok": True} and [c["tool_choice"]["type"] for c in calls] == ["tool", "auto"]


async def test_other_400_errors_are_not_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid API key", "type": "invalid_request_error"}})

    cfg = ProviderConfig(protocol="openai", model="m", api_key="k", base_url="https://bad.test/v1")
    p = build_provider(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception, match="Invalid API key"):
        await p.generate_structured("sys", [Message("user", "hi")], SCHEMA, "report", "d")


def test_compress_html_bem_classes():
    items = "".join(f'<li class="card card--featured"><b>{i}</b></li>' for i in range(10))
    out = compress_html(f"<html><body><ul>{items}</ul></body></html>")
    assert "另有 7 个" in out and "--" not in out.split("另有")[1].split("-->")[0]
