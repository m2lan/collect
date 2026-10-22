import asyncio
from datetime import timedelta

from sqlalchemy import update

from app import scheduler
from app.db import SessionLocal
from app.models import Lot
from app.models.entities import utcnow
from tests.conftest import RULE
from tests.test_api import client, wait_run  # noqa: F401
from tests.test_health import webhook  # noqa: F401
from tests.test_m4 import dyn  # noqa: F401

RULE1 = {**RULE, "pagination": {"type": "none"}}


async def crawl(client, sid, kind="full"):
    return await wait_run(client, (await client.post(f"/api/sources/{sid}/crawl", params={"kind": kind})).json()["id"])


async def until(cond, timeout: float = 5):
    """订阅通知在采集任务结束后发送，等待其到达。"""
    for _ in range(int(timeout * 20)):
        if cond():
            return
        await asyncio.sleep(0.05)


def texts(received):
    return [m["text"]["content"] for m in received]


async def test_subscription_events(client, dyn, webhook):
    _, base = dyn
    url, received = webhook
    src = (await client.post("/api/sources", json={"name": "瓷器站", "start_url": f"{base}/list", "rate_limit": 20, "rule": RULE1})).json()
    bad = await client.post("/api/subscriptions", json={"name": "x", "events": ["price_reach"]})
    assert bad.status_code == 400
    sub = (await client.post("/api/subscriptions", json={
        "name": "青花", "keywords": ["青花", " "], "exclude_keywords": ["民国"], "events": ["new", "price_change", "price_reach"],
        "target_price": 13500, "webhook_type": "wecom", "webhook_url": url,
    })).json()
    assert sub["keywords"] == ["青花"]
    other = (await client.post("/api/subscriptions", json={"name": "高价", "min_price": 30000, "events": ["new"],
                                                            "webhook_type": "wecom", "webhook_url": url})).json()

    await crawl(client, src["id"])  # 动态站：青花瓶价格 13000
    await until(lambda: len(received) >= 2)
    msgs = texts(received)
    assert any("订阅「青花」新上架 1 件" in m and "清乾隆 青花缠枝莲纹瓶" in m for m in msgs)
    assert any("订阅「高价」新上架 1 件" in m and "民国 粉彩人物盘" in m for m in msgs)
    assert not any("价格达到目标" in m for m in msgs)

    received.clear()
    await crawl(client, src["id"])  # 价格涨到 13500：价格变化 + 达到目标；不会重复“新上架”
    await until(lambda: len(received) >= 2)
    msgs = texts(received)
    assert len(msgs) == 2 and any("价格变化" in m and "原 ¥13,000" in m for m in msgs)
    assert any("价格达到目标" in m and "≥ 目标 13,500" in m for m in msgs)

    received.clear()
    await crawl(client, src["id"])  # 再次变化只有价格变化（目标价只提醒一次）
    await until(lambda: len(received) >= 1)
    await asyncio.sleep(0.3)
    assert len(received) == 1 and "价格变化" in texts(received)[0]

    notes = (await client.get("/api/subscriptions/notifications", params={"subscription_id": sub["id"]})).json()
    assert notes["total"] == 4 and all(n["sent"] for n in notes["items"])
    subs = {s["name"]: s for s in (await client.get("/api/subscriptions")).json()}
    assert subs["青花"]["notified"] == 4 and subs["高价"]["notified"] == 1
    assert (await client.post(f"/api/subscriptions/{other['id']}/test")).json()["ok"]


async def test_ending_soon_and_boost(client, dyn, webhook):
    srv, base = dyn
    url, received = webhook
    await client.put("/api/settings/alert", json={"failure_threshold": 2, "webhook_type": "dingtalk", "webhook_url": url})
    src = (await client.post("/api/sources", json={
        "name": "盯价站", "start_url": f"{base}/list", "rate_limit": 20, "rule": RULE1, "ending_boost": True,
    })).json()
    await client.post("/api/subscriptions", json={"name": "快结束", "events": ["ending_soon"], "ending_minutes": 20})
    await crawl(client, src["id"])
    assert await scheduler.boost_tick() == 0  # 还没有临近结束的拍品
    assert await scheduler.ending_notice_tick() == 0

    async with SessionLocal() as s:  # 让进行中的拍品 10 分钟后结束
        await s.execute(update(Lot).where(Lot.status == "live").values(end_time=utcnow() + timedelta(minutes=10)))
        await s.commit()

    # 即将结束提醒（使用全局告警通道），只提醒一次
    assert await scheduler.ending_notice_tick() == 1
    assert "即将结束" in received[-1]["text"]["content"] and "约 9 分钟后结束" in received[-1]["text"]["content"]
    assert await scheduler.ending_notice_tick() == 0

    # 加密盯价：创建只刷新临近结束拍品的任务，间隔内不重复创建
    hits = srv.hits
    assert await scheduler.boost_tick() == 1
    runs = (await client.get("/api/runs", params={"source_id": src["id"]})).json()["items"]
    assert runs[0]["kind"] == "ending" and runs[0]["trigger"] == "boost"
    run = await wait_run(client, runs[0]["id"])
    assert run["status"] == "success" and run["items_found"] == 1 and run["items_updated"] == 1
    assert any("30 分钟内结束" in e["msg"] for e in run["log"]) and srv.hits == hits + 1
    assert await scheduler.boost_tick() == 0
