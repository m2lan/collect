import asyncio
import os
import shutil

import httpx
import pytest

from app.main import app, init_admin
from tests import site
from tests.conftest import RULE

HAS_BROWSER = bool(os.environ.get("BROWSER_EXECUTABLE_PATH") or shutil.which("chromium"))


@pytest.fixture
async def client(db):
    await init_admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c


@pytest.fixture
def server():
    srv, base = site.start()
    yield base
    srv.shutdown()


@pytest.fixture
def login_server():
    srv, base = site.start(require_login=True)
    yield base
    srv.shutdown()


async def wait_run(client: httpx.AsyncClient, run_id: int) -> dict:
    for _ in range(300):
        run = (await client.get(f"/api/runs/{run_id}")).json()
        if run["status"] not in ("pending", "running"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError("run did not finish")


async def test_requires_auth(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/sources")).status_code == 401
        bad = await c.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert bad.status_code == 401


async def test_static_crawl_flow(client, server):
    body = {"name": "测试站", "start_url": f"{server}/list", "fetch_mode": "static", "rate_limit": 20, "rule": RULE}

    preview = await client.post("/api/preview", json={**body, "max_pages": 1})
    assert preview.status_code == 200, preview.text
    assert preview.json()["count"] == 2

    created = (await client.post("/api/sources", json=body)).json()
    assert created["active_rule_version"] == 1

    run = (await client.post(f"/api/sources/{created['id']}/crawl")).json()
    run = await wait_run(client, run["id"])
    assert run["status"] == "success", run
    assert (run["pages"], run["items_found"], run["items_new"]) == (2, 3, 3)

    lots = (await client.get("/api/lots", params={"sort": "-current_price"})).json()
    assert lots["total"] == 3
    top = lots["items"][0]
    assert top["title"] == "民国 粉彩人物盘" and top["source_name"] == "测试站"
    assert top["current_price"] == "32000.00" and top["currency"] == "CNY"
    assert top["extra"]["lot_no"] == "1002"
    assert (await client.get("/api/lots", params={"status": "ended"})).json()["total"] == 1
    assert (await client.get("/api/lots", params={"keyword": "建窑"})).json()["total"] == 1

    # 再次采集：价格未变，不新增也不产生新的价格快照
    run2 = await wait_run(client, (await client.post(f"/api/sources/{created['id']}/crawl")).json()["id"])
    assert (run2["items_new"], run2["items_updated"]) == (0, 0)
    detail = (await client.get(f"/api/lots/{top['id']}")).json()
    assert len(detail["snapshots"]) == 1


async def test_rule_versions(client, server):
    src = (await client.post("/api/sources", json={"name": "s", "start_url": f"{server}/list", "rule": RULE})).json()
    v2 = (await client.post(f"/api/sources/{src['id']}/rules", json={"config": RULE, "note": "v2"})).json()
    rules = (await client.get(f"/api/sources/{src['id']}/rules")).json()
    assert [r["is_active"] for r in rules] == [True, False] and v2["version"] == 2
    await client.post(f"/api/sources/{src['id']}/rules/{rules[1]['id']}/activate")
    assert (await client.get(f"/api/sources/{src['id']}")).json()["active_rule_version"] == 1


async def test_cookie_auth(client, login_server):
    body = {
        "name": "cookie站", "start_url": f"{login_server}/list", "auth_type": "cookie",
        "auth": {"cookies": "sid=ok; other=1"}, "rule": {**RULE, "pagination": {"type": "none"}},
    }
    no_cookie = await client.post("/api/preview", json={**body, "auth_type": "none"})
    assert no_cookie.json()["count"] == 0
    src = (await client.post("/api/sources", json=body)).json()
    assert src["auth"]["cookie_count"] == 2
    # 编辑时不传 cookie，保留原值
    updated = (await client.put(f"/api/sources/{src['id']}", json={**body, "auth": {}})).json()
    assert updated["auth"]["cookie_count"] == 2
    preview = await client.post("/api/preview", json={**body, "auth": {}, "source_id": src["id"]})
    assert preview.json()["count"] == 2


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_form_login_browser(client, login_server):
    body = {
        "name": "表单登录站", "start_url": f"{login_server}/list", "fetch_mode": "browser", "auth_type": "form",
        "auth": {
            "login_url": f"{login_server}/login", "username": "alice", "password": "secret",
            "username_selector": "#u", "password_selector": "#p", "submit_selector": "#go",
            "logged_in_selector": "#user",
        },
        "rule": RULE,
    }
    src = (await client.post("/api/sources", json=body)).json()
    assert src["auth"]["has_password"] and "password" not in src["auth"]
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "success", run
    assert run["items_found"] == 3
    assert (await client.get(f"/api/sources/{src['id']}")).json()["auth"]["has_session"]


@pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")
async def test_form_login_static_mode(client, login_server):
    """静态模式下由浏览器完成登录，再用 httpx 携带会话 Cookie 抓取。"""
    body = {
        "name": "静态+登录", "start_url": f"{login_server}/list", "fetch_mode": "static", "auth_type": "form",
        "auth": {
            "login_url": f"{login_server}/login", "username": "alice", "password": "secret",
            "username_selector": "#u", "password_selector": "#p", "submit_selector": "#go",
            "logged_in_selector": "#user",
        },
        "rule": RULE,
    }
    preview = await client.post("/api/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["count"] == 2
