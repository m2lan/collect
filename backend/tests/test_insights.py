import json
from decimal import Decimal

from sqlalchemy import update

from app.ai import service
from app.ai.providers import LLMResult, ProviderConfig
from app.analytics import _grams
from app.db import SessionLocal
from app.models import Lot
from app.search import expand, parse_synonyms
from tests.conftest import RULE
from tests.test_api import client, server, wait_run  # noqa: F401
from tests.test_health import webhook  # noqa: F401

RULE1 = {**RULE, "pagination": {"type": "next_link", "selector": "a.next", "max_pages": 2}}


class Tagger:
    """按标题给出标签的假模型。"""

    def __init__(self):
        self.config = ProviderConfig(protocol="openai", model="m", api_key="k")
        self.calls = 0

    async def generate_structured(self, system, messages, schema, tool_name, tool_description):
        assert tool_name == "submit_tags" and "瓷器陶器" in system
        self.calls += 1
        out = []
        for lot in json.loads(messages[0].content):
            t = lot["title"]
            if "瓶" in t or "盘" in t or "盏" in t:
                out.append({"id": lot["id"], "category": "瓷器陶器", "era": t[:2], "keywords": ["瓷器", "", "收藏"]})
            else:
                out.append({"id": lot["id"], "category": "不存在的品类"})
        return LLMResult({"items": out}, prompt_tokens=50, completion_tokens=20)


async def _setup(client, server, monkeypatch, auto_tag=True):
    tagger = Tagger()
    monkeypatch.setattr(service, "provider_factory", lambda cfg: tagger)
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 0, "auto_repair": False, "max_rounds": 2, "auto_tag": auto_tag})
    await client.post("/api/ai/providers", json={"name": "c", "protocol": "openai", "api_key": "sk-1234567890ab", "model": "m"})
    a = (await client.post("/api/sources", json={"name": "甲站", "start_url": f"{server}/list", "rate_limit": 20, "rule": RULE1})).json()
    await wait_run(client, (await client.post(f"/api/sources/{a['id']}/crawl")).json()["id"])
    return tagger, a


def test_synonyms_and_grams():
    groups = parse_synonyms("翡翠,玉翠、缅玉\n单个")
    assert expand("翡翠", groups) == {"翡翠", "玉翠", "缅玉"} and expand("瓷", groups) == {"瓷"}
    assert {"青花", "花瓷"} <= _grams("清乾隆 青花瓷瓶") and "rolex" in _grams("Rolex 劳力士")


async def test_auto_tag_search_facets(client, server, monkeypatch):
    tagger, _ = await _setup(client, server, monkeypatch)
    lots = (await client.get("/api/lots", params={"page_size": 50})).json()["items"]
    assert all(l["tags"]["category"] == "瓷器陶器" for l in lots) and tagger.calls == 1
    assert lots[0]["tags"]["keywords"] == ["瓷器", "收藏"]

    facets = (await client.get("/api/lots/facets")).json()
    assert facets["category"] == [{"value": "瓷器陶器", "count": 3}] and facets["untagged"] == 0
    assert (await client.get("/api/lots", params={"category": "瓷器陶器"})).json()["total"] == 3
    assert (await client.get("/api/lots", params={"era": "清乾"})).json()["total"] == 1

    # 多关键词 AND；标签也参与搜索；同义词扩展
    assert (await client.get("/api/lots", params={"keyword": "青花 乾隆"})).json()["total"] == 1
    assert (await client.get("/api/lots", params={"keyword": "青花 民国"})).json()["total"] == 0
    assert (await client.get("/api/lots", params={"keyword": "收藏"})).json()["total"] == 3
    assert (await client.get("/api/lots", params={"keyword": "兔毫"})).json()["total"] == 1
    await client.put("/api/settings/search", json={"synonyms": "建盏,兔毫"})
    assert (await client.get("/api/lots", params={"keyword": "建盏"})).json()["total"] == 1

    # 手动批量标注：未标注的拍品
    async with SessionLocal() as s:
        await s.execute(update(Lot).values(tagged_at=None))
        await s.commit()
    r = (await client.post("/api/ai/tag", json={"limit": 10})).json()
    assert r == {"tagged": 3, "batches": 1, "failed": 0}
    gens = (await client.get("/api/ai/generations")).json()["items"]
    assert gens[0]["trigger"] == "tag" and gens[0]["item_count"] == 3


async def test_similar_and_analytics(client, server, monkeypatch, webhook):
    url, received = webhook
    await _setup(client, server, monkeypatch)
    # 第二个平台有同款
    b = (await client.post("/api/sources", json={"name": "乙站", "start_url": f"{server}/list", "rate_limit": 20, "rule": RULE1})).json()
    await wait_run(client, (await client.post(f"/api/sources/{b['id']}/crawl")).json()["id"])
    async with SessionLocal() as s:
        await s.execute(update(Lot).where(Lot.source_id == b["id"], Lot.title.like("%青花%")).values(current_price=Decimal("15000")))
        await s.execute(update(Lot).where(Lot.title.like("%民国%")).values(start_price=Decimal("20000"), deal_price=Decimal("32000")))
        await s.commit()

    lots = (await client.get("/api/lots", params={"keyword": "青花", "source_id": 1})).json()["items"]
    sim = (await client.get(f"/api/lots/{lots[0]['id']}/similar")).json()
    top = sim["items"][0]
    assert top["source_name"] == "乙站" and top["similarity"] == 1.0 and not top["same_source"]
    assert sim["stats"]["count"] >= 1 and sim["stats"]["max"] == 15000.0
    assert sim["stats"]["own_vs_median"] is not None

    a = (await client.get("/api/analytics", params={"days": 30})).json()
    s = a["summary"]
    assert s["lots"] == 6 and s["sold"] == 2 and s["closed"] == 2 and s["sold_rate"] == 1.0
    assert s["premium_median"] == 0.6 and s["deal_price"]["median"] == 32000.0
    assert a["by_category"][0]["name"] == "瓷器陶器" and {x["name"] for x in a["by_source"]} == {"甲站", "乙站"}
    assert a["trend"] and sum(t["lots"] for t in a["trend"]) == 6
    assert (await client.get("/api/analytics", params={"keyword": "青花"})).json()["summary"]["lots"] == 2

    await client.put("/api/settings/alert", json={"failure_threshold": 2, "webhook_type": "generic", "webhook_url": url})
    rep = (await client.post("/api/analytics/report", json={"period": "week", "push": True})).json()
    assert rep["title"].startswith("拍卖行情周报") and "成交率 100.0%" in rep["markdown"] and "## 分品类" in rep["markdown"]
    assert received[-1]["title"] == rep["title"]
