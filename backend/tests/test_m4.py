from decimal import Decimal

import pytest

from app.ai import service
from app.crawler.extractor import extract_json
from app.crawler.sniff import draft_rule, find_candidates
from app.schemas.rule import RuleConfig
from tests import site
from tests.conftest import RULE
from tests.test_ai import FakeProvider
from tests.test_api import HAS_BROWSER, client, wait_run  # noqa: F401

API_FIELDS = [
    {"key": "title", "type": "jsonpath", "selector": "$.lotName", "transform": ["trim"]},
    {"key": "current_price", "type": "jsonpath", "selector": "$.currentPrice", "transform": ["price"]},
    {"key": "bid_count", "type": "jsonpath", "selector": "bidCount", "transform": ["int"]},
    {"key": "status", "type": "jsonpath", "selector": "$.status", "transform": ["status"]},
    {"key": "url", "type": "jsonpath", "selector": "$.detailUrl", "transform": ["absolute_url"]},
    {"key": "lot_no", "type": "jsonpath", "selector": "$.id"},
]


@pytest.fixture
def dyn():
    srv, base = site.start(dynamic=True)
    yield srv, base
    srv.shutdown()


def api_rule(base: str, **pagination) -> dict:
    return {
        "fetch": {"mode": "api", "url": f"{base}/api/lots?page=1"},
        "list": {"item_selector": "$.data.list[*]", "item_selector_type": "jsonpath", "unique_key": "lot_no", "fields": API_FIELDS},
        "pagination": pagination or {"type": "none"},
    }


def test_rule_validation_api_mode():
    with pytest.raises(ValueError):
        RuleConfig.model_validate({"fetch": {"mode": "api"}, "list": {"item_selector": ".x", "fields": [{"key": "t"}]}})
    with pytest.raises(ValueError):
        RuleConfig.model_validate({**api_rule("http://x"), "pagination": {"type": "next_link", "selector": "a"}})


def test_extract_json_and_sniff():
    config = RuleConfig.model_validate(api_rule("http://x"))
    page = extract_json(site.lots_page(1), "http://x/api/lots", config)
    assert [it["title"] for it in page.items] == ["清乾隆 青花缠枝莲纹瓶", "民国 粉彩人物盘"]
    assert page.items[0]["current_price"] == Decimal("12500.00") and page.items[0]["lot_no"] == 1001
    assert page.items[0]["url"] == "http://x/lot/1001"

    cands = find_candidates([{"url": "http://x/api/lots?page=1", "method": "GET", "data": site.lots_page(1)}])
    assert cands[0]["items_path"] == "$.data.list[*]"
    keys = {f["key"] for f in cands[0]["guessed_fields"]}
    assert {"title", "current_price", "bid_count", "status", "end_time", "image", "url", "lot_no"} <= keys
    draft = RuleConfig.model_validate(draft_rule(cands[0]))
    assert len(extract_json(site.lots_page(1), "http://x", draft).items) == 2


async def test_api_mode_get_and_post_pagination(client, dyn):
    _, base = dyn
    rule = api_rule(base, type="page_param", param="page", max_pages=5)
    src = (await client.post("/api/sources", json={"name": "接口站", "start_url": f"{base}/spa", "rate_limit": 20, "rule": rule})).json()
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    # 第 3 页为空时停止翻页
    assert run["status"] == "success" and run["items_found"] == 3 and run["pages"] == 3, run

    post_rule = {**rule, "fetch": {"mode": "api", "url": f"{base}/api/search", "method": "POST", "body": {"pageSize": 2}},
                 "pagination": {"type": "page_param", "param": "pageNo", "max_pages": 3}}
    preview = (await client.post("/api/preview", json={"start_url": f"{base}/spa", "rule": post_rule, "max_pages": 3})).json()
    assert preview["count"] == 3 and preview["pages"] == 3


async def test_detail_pages_and_live_watch(client, dyn):
    srv, base = dyn
    rule = {
        **RULE,
        "pagination": {"type": "none"},
        "detail": {
            "enabled": True,
            "fields": [
                {"key": "estimate", "selector": ".estimate", "transform": [{"name": "regex_replace", "pattern": "^估价：", "repl": ""}]},
                {"key": "deposit", "selector": ".deposit", "transform": ["price"]},
                {"key": "current_price", "selector": ".price-box .now", "transform": ["price"]},
            ],
        },
    }
    src = (await client.post("/api/sources", json={"name": "详情站", "start_url": f"{base}/list", "rate_limit": 20, "rule": rule})).json()
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "success", run
    assert any("详情页完成：成功 2" in e["msg"] for e in run["log"])
    lots = (await client.get("/api/lots", params={"source_id": src["id"], "sort": "-current_price"})).json()["items"]
    live = next(x for x in lots if x["status"] == "live")
    assert live["extra"]["estimate"] == "RMB 80,000-120,000" and live["extra"]["deposit"] == "5000"
    # 详情页价格（动态 +1000/次）覆盖列表价
    first_price = Decimal(live["current_price"])

    # only_new：第二次完整采集不再抓取已有拍品的详情页
    hits = srv.detail_hits
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert srv.detail_hits == hits and any("没有需要抓取详情页" in e["msg"] for e in run["log"])

    # 盯价：只刷新进行中的拍品（1 个），通过详情页更新价格
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl", params={"kind": "live"})).json()["id"])
    assert run["kind"] == "live" and run["status"] == "success" and run["items_found"] == 1 and run["items_updated"] == 1, run
    detail = (await client.get(f"/api/lots/{live['id']}")).json()
    assert Decimal(detail["current_price"]) > first_price and len(detail["snapshots"]) >= 2


async def test_live_watch_schedule_kind(client, dyn):
    _, base = dyn
    src = (await client.post("/api/sources", json={"name": "s", "start_url": f"{base}/list", "rule": RULE})).json()
    saved = (await client.put(f"/api/sources/{src['id']}/schedules", json=[
        {"kind": "full", "cron": "0 * * * *"}, {"kind": "live", "cron": "*/5 * * * *"},
    ])).json()
    assert {s["kind"] for s in saved} == {"full", "live"}


async def test_proxy_and_crawler_settings(client, dyn):
    _, base = dyn
    proxy_srv, proxy = site.start_proxy()
    try:
        r = (await client.put("/api/settings/crawler", json={"proxies": [proxy, " "], "random_ua": True})).json()
        assert r["proxies"] == [proxy]
        rule = {**RULE, "pagination": {"type": "none"}}
        src = (await client.post("/api/sources", json={"name": "代理站", "start_url": f"{base}/list", "use_proxy": True, "rule": rule})).json()
        run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
        assert run["status"] == "success", run
        assert any(u.endswith("/list") for u in proxy_srv.seen)
        assert any("代理" in e["msg"] for e in run["log"])
    finally:
        proxy_srv.shutdown()


async def test_retry_on_server_error(monkeypatch):
    from app.crawler import runner

    monkeypatch.setattr(runner.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    class Flaky:
        async def fetch(self, url, method="GET", body=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("boom")
            return "ok"

    log = runner.RunLog()
    assert await runner.fetch_with_retry(Flaky(), "http://x", log) == "ok"
    assert calls["n"] == 3 and len([e for e in log.entries if e["level"] == "warn"]) == 2


async def _no_sleep(*_):
    return None


async def test_templates(client):
    t = (await client.post("/api/templates", json={"name": "某拍卖平台", "description": "列表页通用", "fetch_mode": "browser", "config": RULE})).json()
    assert t["config"]["list"]["item_selector"] == ".lot-item"
    assert len((await client.get("/api/templates")).json()) == 1
    await client.delete(f"/api/templates/{t['id']}")
    assert (await client.get("/api/templates")).json() == []


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_sniff_and_ai_api_mode_with_vision(client, dyn, monkeypatch):
    _, base = dyn
    r = (await client.post("/api/sniff", json={"start_url": f"{base}/spa"})).json()
    cand = r["candidates"][0]
    assert cand["url"].endswith("/api/lots?page=1") and cand["count"] == 2
    preview = (await client.post("/api/preview", json={"start_url": f"{base}/spa", "rule": cand["rule"]})).json()
    assert preview["count"] == 2

    # AI：提示中带有捕获到的接口与截图，生成的 api 规则基于接口数据试跑
    provider = FakeProvider([{"explanation": "使用接口", "rule": api_rule(base)}])
    provider.config.supports_vision = True
    monkeypatch.setattr(service, "provider_factory", lambda cfg: provider)
    await client.put("/api/ai/settings", json={"enabled": True, "daily_limit": 0, "auto_repair": False, "max_rounds": 2})
    await client.post("/api/ai/providers", json={"name": "v", "protocol": "openai", "api_key": "sk-1234567890ab", "model": "m", "supports_vision": True})
    out = (await client.post("/api/ai/generate", json={"start_url": f"{base}/spa", "fetch_mode": "browser"})).json()
    assert out["ok"] and out["count"] == 2, out
    first = provider.calls[0][0]
    assert "$.data.list[*]" in first.content and first.images and first.images[0][:4] == b"\x89PNG"
