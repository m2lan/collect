import pytest

from app.remote import manager
from tests.conftest import RULE
from tests.test_api import HAS_BROWSER, client, login_server, wait_run  # noqa: F401
from tests.test_health import webhook  # noqa: F401

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason="未配置浏览器")


async def _center(sid: str, selector: str) -> tuple[float, float]:
    box = await manager.get(sid).page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def test_remote_login_session_and_expiry_alert(client, login_server, webhook):
    url, received = webhook
    await client.put("/api/settings/alert", json={"failure_threshold": 5, "webhook_type": "feishu", "webhook_url": url})
    body = {
        "name": "需要扫码的站", "start_url": f"{login_server}/list", "rate_limit": 20, "auth_type": "session",
        "auth": {"logged_in_selector": "#user"}, "rule": {**RULE, "pagination": {"type": "none"}},
    }
    src = (await client.post("/api/sources", json=body)).json()

    # 未登录：采集失败并提醒一次
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "failed" and "登录已失效" in run["error"]
    assert (await client.get(f"/api/sources/{src['id']}")).json()["login_expired_at"]
    assert sum("登录失效" in m["content"]["text"] for m in received) == 1

    # 远程浏览器：打开登录页，像人一样点击、输入
    st = (await client.post("/api/remote/start", json={"source_id": src["id"], "url": f"{login_server}/login"})).json()
    sid = st["session_id"]
    shot = await client.get(f"/api/remote/{sid}/screenshot")
    assert shot.headers["content-type"] == "image/jpeg" and shot.content[:2] == b"\xff\xd8"
    for selector, text in (("#u", "alice"), ("#p", "secret")):
        x, y = await _center(sid, selector)
        await client.post(f"/api/remote/{sid}/action", json={"type": "click", "x": x, "y": y})
        await client.post(f"/api/remote/{sid}/action", json={"type": "type", "text": text})
    x, y = await _center(sid, "#go")
    state = (await client.post(f"/api/remote/{sid}/action", json={"type": "click", "x": x, "y": y})).json()
    await manager.get(sid).page.wait_for_url("**/list")
    saved = (await client.post(f"/api/remote/{sid}/save")).json()
    assert saved["ok"] and saved["cookies"] >= 1 and saved["auth_type"] == "session"
    await client.delete(f"/api/remote/{sid}")
    assert (await client.get(f"/api/remote/{sid}/state")).status_code == 404

    s = (await client.get(f"/api/sources/{src['id']}")).json()
    assert s["auth"]["has_session"] and s["auth"]["session_saved_at"] and s["login_expired_at"] is None

    # 使用保存的会话采集成功（静态模式也能复用浏览器会话的 Cookie）
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "success" and run["items_found"] == 2, run

    # 服务端会话失效 → 采集失败并提醒；再次失败不重复提醒
    from tests.test_api import _servers

    _servers["login"].valid_sid = "rotated"
    for _ in range(2):
        run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
        assert run["status"] == "failed" and "远程登录" in run["error"]
    assert sum("登录失效" in m["content"]["text"] for m in received) == 2  # 第一次未登录 + 这次失效

    # 编辑站点其他设置不会清掉已保存的会话
    upd = (await client.put(f"/api/sources/{src['id']}", json={**body, "name": "改名"})).json()
    assert upd["auth"]["has_session"]

    # 会话恢复有效后采集成功，并发送恢复通知
    _servers["login"].valid_sid = "ok"
    run = await wait_run(client, (await client.post(f"/api/sources/{src['id']}/crawl")).json()["id"])
    assert run["status"] == "success"
    assert (await client.get(f"/api/sources/{src['id']}")).json()["login_expired_at"] is None
    assert "登录已恢复" in received[-1]["content"]["text"]
