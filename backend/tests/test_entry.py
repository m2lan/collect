"""入口页（拍卖会 / 专场列表 → 各专场拍品）。"""

import pytest
from pydantic import ValidationError

from app.schemas.rule import RuleConfig
from tests.conftest import RULE
from tests.test_api import client, server  # noqa: F401
from tests.test_pagination_detail import ScriptedProvider, enable_ai

ENTRY_RULE = {**RULE, "entry": {"link_selector": ".event a.view"}}


async def test_entry_crawls_every_event(client, server):
    body = {"start_url": f"{server}/events", "fetch_mode": "static", "rule": ENTRY_RULE, "max_pages": 2}
    r = await client.post("/api/preview", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    urls = [it["url"] for it in out["items"]]
    # 专场 1 两页共 3 件，专场 2 一页 2 件；专场本身不会被当成拍品
    assert out["count"] == 5 and f"{server}/lot/2001" in urls and f"{server}/lot/1003" in urls
    assert not any("/event/" in u for u in urls)
    log = "\n".join(e["msg"] for e in out["log"])
    assert "找到 2 个专场链接" in log and "专场 2/2" in log


async def test_entry_crawl_saves_lots(client, server):
    from tests.test_api import wait_run

    src = (await client.post("/api/sources", json={"name": "专场站", "start_url": f"{server}/events", "rule": ENTRY_RULE})).json()
    run = (await client.post(f"/api/sources/{src['id']}/crawl")).json()
    run = await wait_run(client, run["id"])
    assert run["status"] == "success" and run["items_found"] == 5 and run["pages"] == 3


async def test_entry_without_links_logs_snippet(client, server):
    body = {"start_url": f"{server}/events", "fetch_mode": "static",
            "rule": {**RULE, "entry": {"link_selector": ".nothing a"}}}
    out = (await client.post("/api/preview", json=body)).json()
    assert out["count"] == 0
    assert any("没有找到专场链接" in e["msg"] for e in out["log"])


def test_entry_rejects_url_template():
    with pytest.raises(ValidationError, match="url_template"):
        RuleConfig.model_validate({**ENTRY_RULE, "pagination": {"type": "url_template", "template": "/x?p={page}"}})


async def test_ai_detects_event_index(client, server, monkeypatch):
    provider = ScriptedProvider({"submit_rule": [
        {"explanation": "页面是拍卖会列表", "page_kind": "events", "entry_link_selector": ".event h3"},  # 选择器不对
        {"explanation": "页面是拍卖会列表", "page_kind": "events", "entry_link_selector": ".event a.view"},
        {"explanation": "专场内的拍品列表", "page_kind": "lots", "rule": RULE},
    ]})
    await enable_ai(client, monkeypatch, provider)
    r = await client.post("/api/ai/generate", json={"start_url": f"{server}/events", "fetch_mode": "static"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"], out
    assert out["rule"]["entry"]["link_selector"] == ".event a.view"
    assert "找到 2 个专场" in out["explanation"] and "专场内的拍品列表" in out["explanation"]
    assert all("/lot/" in it["url"] for it in out["items"])
    # 选择器没匹配到链接时把问题反馈给 AI；第三次请求基于第一个专场页面
    assert "没有匹配到带 href 的专场链接" in provider.calls[1][1][-1].content
    third = provider.calls[2][1][0].content
    assert f"{server}/event/1" in third and "入口页共找到 2 个专场" in third
