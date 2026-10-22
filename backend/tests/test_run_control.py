"""采集任务控制：页面卡死超时、翻页中途失败保留数据、手动停止、僵死任务清理。"""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.crawler import runner
from app.crawler.fetchers.base import Fetched
from app.crawler.runner import RunLog, crawl, fetch_with_retry
from app.db import SessionLocal
from app.models import CrawlRun
from app.models.entities import utcnow
from app.scheduler import sweep_stale_runs
from tests.conftest import RULE
from tests.test_api import client, server, wait_run  # noqa: F401


class HangingFetcher:
    """第一次请求永远不返回（模拟浏览器卡死），reset 后恢复正常。"""

    class _Cfg:
        timeout = 1

    fetch_config = _Cfg()

    def __init__(self):
        self.resets = 0
        self.calls = 0

    async def fetch(self, url, method="GET", body=None):
        self.calls += 1
        if self.resets == 0:
            await asyncio.sleep(3600)
        return Fetched(url=url, status=200, html="<html></html>")

    async def reset(self):
        self.resets += 1


async def test_hung_page_times_out_and_resets(monkeypatch):
    monkeypatch.setattr(runner, "hard_timeout", lambda f: 0.2)
    f = HangingFetcher()
    log = RunLog()
    fetched = await fetch_with_retry(f, "http://x/list", log)
    assert fetched.status == 200 and f.resets == 1 and f.calls == 2
    assert "没有加载完成" in log.entries[0]["msg"]


async def test_later_page_failure_keeps_items(server, monkeypatch):
    from app.crawler.fetchers.base import FetchContext
    from app.schemas.rule import RuleConfig

    real = runner.fetch_with_retry

    async def flaky(fetcher, url, *a, **k):
        if "page=2" in url:
            raise TimeoutError("页面 120s 内没有加载完成")
        return await real(fetcher, url, *a, **k)

    monkeypatch.setattr(runner, "fetch_with_retry", flaky)
    log = RunLog()
    res = await crawl(FetchContext(start_url=f"{server}/list"), RuleConfig.model_validate(RULE), rate_limit=0, runlog=log)
    assert len(res.items) == 2 and res.pages == 1
    assert any("第 2 页" in e and "停止翻页" in e for e in res.errors)


async def test_stop_request_saves_partial(client, server, monkeypatch):
    monkeypatch.setattr(runner, "HEARTBEAT_SECONDS", 0.3)
    real = runner.fetch_with_retry

    async def slow(fetcher, url, *a, **k):
        r = await real(fetcher, url, *a, **k)
        if "page=2" not in url:
            await asyncio.sleep(1)  # 第 1 页之后给"停止"留出时间
        return r

    monkeypatch.setattr(runner, "fetch_with_retry", slow)
    src = (await client.post("/api/sources", json={"name": "停止测试", "start_url": f"{server}/list", "rule": RULE})).json()
    run = (await client.post(f"/api/sources/{src['id']}/crawl")).json()
    for _ in range(100):
        cur = (await client.get(f"/api/runs/{run['id']}")).json()
        if cur["status"] == "running":
            break
        await asyncio.sleep(0.02)
    r = await client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelling"
    # 实时日志在执行中就能看到
    for _ in range(50):
        cur = (await client.get(f"/api/runs/{run['id']}")).json()
        if cur["log"]:
            break
        await asyncio.sleep(0.05)
    assert cur["log"] and "开始采集" in cur["log"][0]["msg"]
    done = await wait_run(client, run["id"])
    assert done["status"] == "cancelled" and done["items_found"] == 2 and done["pages"] == 1
    assert "已手动停止" in done["error"]
    assert (await client.post(f"/api/runs/{run['id']}/cancel")).status_code == 400


async def test_cancel_pending_and_sweep_stale(client, server):
    src = (await client.post("/api/sources", json={"name": "清理测试", "start_url": f"{server}/list", "rule": RULE})).json()
    async with SessionLocal() as s:
        pending = CrawlRun(source_id=src["id"], status="pending")
        dead = CrawlRun(source_id=src["id"], status="running", started_at=utcnow() - timedelta(minutes=50))
        alive = CrawlRun(source_id=src["id"], status="running", started_at=utcnow() - timedelta(minutes=50),
                         heartbeat_at=utcnow())
        s.add_all([pending, dead, alive])
        await s.commit()
        ids = pending.id, dead.id, alive.id
    r = await client.post(f"/api/runs/{ids[0]}/cancel")
    assert r.json()["status"] == "cancelled"
    await runner.run_crawl(ids[0])  # 已取消的任务不会再执行
    assert await sweep_stale_runs() == 1
    async with SessionLocal() as s:
        rows = {r.id: r for r in (await s.scalars(select(CrawlRun).where(CrawlRun.id.in_(ids)))).all()}
    assert rows[ids[0]].status == "cancelled" and rows[ids[0]].pages == 0
    assert rows[ids[1]].status == "failed" and "任务中断" in rows[ids[1]].error
    assert rows[ids[2]].status == "running"


async def test_detail_pages_do_not_wait_for_list_selector(server):
    import time

    import pytest

    from app.crawler.fetchers.base import FetchContext
    from app.schemas.rule import RuleConfig
    from tests.test_api import HAS_BROWSER

    if not HAS_BROWSER:
        pytest.skip("未配置浏览器")
    rule = {**RULE, "fetch": {"mode": "browser", "wait_for": ".lot-item", "timeout": 8},
            "detail": {"enabled": True, "fields": [{"key": "estimate", "selector": ".estimate", "transform": ["trim"]}]}}
    log = RunLog()
    started = time.monotonic()
    res = await crawl(FetchContext(start_url=f"{server}/list", fetch_mode="browser"), RuleConfig.model_validate(rule),
                      rate_limit=0, runlog=log)
    # 3 个详情页上都没有 .lot-item；以前每页要等满 8 秒
    assert res.details == 3 and all(it.get("estimate") for it in res.items)
    assert time.monotonic() - started < 15, [e["msg"] for e in log.entries]
