from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app import scheduler
from app.crawler.snapshot import sanitize_snapshot
from app.db import SessionLocal
from app.models import Schedule
from tests.conftest import RULE
from tests.test_api import client, server, wait_run  # noqa: F401


def test_next_fire_uses_timezone():
    # 每天 8 点（北京时间）= UTC 0 点
    nxt = scheduler.next_fire("0 8 * * *", datetime(2026, 10, 21, 1, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 10, 22, 0, 0, tzinfo=UTC)


def test_sanitize_snapshot():
    out = sanitize_snapshot(
        '<html><head><script>alert(1)</script></head><body onload="x()"><a href="javascript:x()">a</a>'
        '<img src="data:image/gif;base64,AA" data-src="/real.jpg"></body></html>',
        "https://site.com/list",
    )
    assert "<script" not in out and "onload" not in out and "javascript:" not in out
    assert '<base href="https://site.com/list">' in out and 'src="/real.jpg"' in out


async def test_schedules_api_and_tick(client, server):
    src = (await client.post("/api/sources", json={"name": "s", "start_url": f"{server}/list", "rate_limit": 20, "rule": RULE})).json()
    bad = await client.put(f"/api/sources/{src['id']}/schedules", json=[{"kind": "full", "cron": "61 * * * *"}])
    assert bad.status_code == 400
    saved = (await client.put(f"/api/sources/{src['id']}/schedules", json=[{"kind": "full", "cron": "*/30 * * * *"}])).json()
    assert len(saved) == 1 and saved[0]["next_run_at"]

    # 未到时间不触发
    assert await scheduler.tick() == 0
    async with SessionLocal() as s:
        await s.execute(update(Schedule).values(next_run_at=datetime.now(UTC) - timedelta(minutes=1)))
        await s.commit()
    assert await scheduler.tick() == 1

    runs = (await client.get("/api/runs", params={"source_id": src["id"]})).json()["items"]
    assert runs[0]["trigger"] == "cron"
    run = await wait_run(client, runs[0]["id"])
    assert run["status"] == "success"
    assert any("第 1 页" in e["msg"] for e in run["log"]) and any("入库完成" in e["msg"] for e in run["log"])

    sched = (await client.get(f"/api/sources/{src['id']}/schedules")).json()[0]
    assert datetime.fromisoformat(sched["next_run_at"]) > datetime.now(UTC)
    assert sched["last_run_at"]

    # 删除定时任务
    assert (await client.put(f"/api/sources/{src['id']}/schedules", json=[])).json() == []


async def test_snapshot_endpoint(client, server):
    r = await client.post("/api/snapshot", json={"start_url": f"{server}/list"})
    assert r.status_code == 200
    assert "lot-item" in r.json()["html"] and "<base" in r.json()["html"]
