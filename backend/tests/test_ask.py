from decimal import Decimal

from sqlalchemy import update

from app.ai import service
from app.ai.providers import LLMResult, ProviderConfig
from app.db import SessionLocal
from app.models import Lot
from tests.conftest import RULE
from tests.test_api import client, server, wait_run  # noqa: F401


class Planner:
    def __init__(self, plan, answer="AI 的回答"):
        self.config = ProviderConfig(protocol="openai", model="m", api_key="k")
        self.plan, self.answer, self.calls = plan, answer, []

    async def generate_structured(self, system, messages, schema, tool_name, tool_description):
        self.calls.append((tool_name, system, messages[0].content))
        if tool_name == "make_plan":
            assert "今天是" in system and "甲站" in system
            return LLMResult(self.plan, prompt_tokens=10, completion_tokens=5)
        return LLMResult({"answer": self.answer}, prompt_tokens=3, completion_tokens=2)


async def _setup(client, server, monkeypatch, plan, answer="AI 的回答"):
    p = Planner(plan, answer)
    monkeypatch.setattr(service, "provider_factory", lambda cfg: p)
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 0, "auto_repair": False, "max_rounds": 2})
    await client.post("/api/ai/providers", json={"name": "c", "protocol": "openai", "api_key": "sk-1234567890ab", "model": "m"})
    src = (await client.post("/api/sources", json={"name": "甲站", "start_url": f"{server}/list", "rate_limit": 20, "rule": RULE})).json()
    await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    async with SessionLocal() as s:
        await s.execute(update(Lot).where(Lot.title.like("%青花%")).values(tags={"category": "瓷器陶器", "era": "清"}))
        await s.execute(update(Lot).where(Lot.title.like("%民国%")).values(tags={"category": "瓷器陶器", "era": "民国"}))
        await s.execute(update(Lot).where(Lot.title.like("%建窑%")).values(tags={"category": "瓷器陶器", "era": "宋"}, current_price=Decimal("5600")))
        await s.commit()
    return p


async def test_ask_aggregate_number(client, server, monkeypatch):
    p = await _setup(client, server, monkeypatch, {
        "mode": "aggregate", "filters": {"category": "瓷器陶器", "status": "ended", "days": 0},
        "metric": "avg_price", "group_by": "none", "chart": "number", "title": "瓷器成交均价",
    }, answer="瓷器成交均价为 32,000 元。")
    r = (await client.post("/api/ai/ask", json={"question": "瓷器成交均价是多少"})).json()
    assert r["rows"] == [{"label": "平均价", "value": 32000.0}] and r["total"] == 1
    assert r["answer"] == "瓷器成交均价为 32,000 元。" and [c[0] for c in p.calls] == ["make_plan", "answer"]
    gen = (await client.get("/api/ai/generations")).json()["items"][0]
    assert gen["trigger"] == "ask" and gen["prompt_tokens"] == 13


async def test_ask_group_and_list(client, server, monkeypatch):
    await _setup(client, server, monkeypatch, {
        "mode": "aggregate", "filters": {}, "metric": "count", "group_by": "era", "chart": "pie", "title": "各年代数量",
    })
    r = (await client.post("/api/ai/ask", json={"question": "各年代的拍品各有多少"})).json()
    assert {x["label"]: x["value"] for x in r["rows"]} == {"清": 1.0, "民国": 1.0, "宋": 1.0} and r["group_label"] == "年代"

    # 列表：价格从高到低，取前 2
    from tests import test_ask

    p = test_ask.Planner({"mode": "list", "filters": {"source": "甲"}, "sort": "-price", "limit": 2, "chart": "table", "title": "最贵的"})
    monkeypatch.setattr(service, "provider_factory", lambda cfg: p)
    r = (await client.post("/api/ai/ask", json={"question": "甲站最贵的两件是什么"})).json()
    assert [x["title"] for x in r["rows"]] == ["民国 粉彩人物盘", "清乾隆 青花缠枝莲纹瓶"] and r["total"] == 3
    assert r["rows"][0]["source"] == "甲站" and r["rows"][0]["status"] == "已成交"

    # 价格区间 + 关键词，没有结果时的模板回答（AI 回答失败也可用）
    from app.ai.ask import execute_plan, fallback_answer

    async with SessionLocal() as s:
        plan = {"mode": "aggregate", "filters": {"keyword": "青花", "min_price": 20000}, "metric": "count", "chart": "number"}
        res = await execute_plan(s, plan)
    assert res["rows"][0]["value"] == 0 and fallback_answer(plan, res) == "没有找到符合条件的拍品。"
