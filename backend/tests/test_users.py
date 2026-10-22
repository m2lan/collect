import httpx

from app.main import app
from tests.conftest import RULE
from tests.test_api import client, server  # noqa: F401


async def login(username: str, password: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


async def test_roles_and_audit(client, server):
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "admin" and me["role_label"] == "管理员"
    for name, role in (("op", "operator"), ("view", "viewer")):
        r = await client.post("/api/users", json={"username": name, "role": role, "password": "pass1234", "display_name": name.upper()})
        assert r.status_code == 201, r.text
    assert (await client.post("/api/users", json={"username": "op", "password": "pass1234"})).status_code == 409

    op, view = await login("op", "pass1234"), await login("view", "pass1234")
    try:
        # 只读：能看不能改
        assert (await view.get("/api/lots")).status_code == 200
        assert (await view.get("/api/dashboard")).status_code == 200
        assert (await view.post("/api/sources", json={"name": "x", "start_url": f"{server}/list"})).status_code == 403
        assert (await view.post("/api/preview", json={"start_url": f"{server}/list", "rule": RULE})).status_code == 403
        assert (await view.get("/api/users")).status_code == 403
        # 操作员：管理站点与采集，不能改系统设置、模型、用户、备份
        src = await op.post("/api/sources", json={"name": "op 的站", "start_url": f"{server}/list", "rule": RULE})
        assert src.status_code == 201
        assert (await op.post(f"/api/sources/{src.json()['id']}/crawl")).status_code == 202
        assert (await op.get("/api/ai/settings")).status_code == 200
        assert (await op.put("/api/ai/settings", json={"enabled": True})).status_code == 403
        assert (await op.get("/api/ai/providers")).status_code == 403
        assert (await op.get("/api/settings/alert")).status_code == 403
        assert (await op.get("/api/backups")).status_code == 403
        assert (await op.get("/api/users")).status_code == 403
        # 修改自己的密码
        assert (await op.put("/api/auth/password", json={"old_password": "bad", "new_password": "newpass1"})).status_code == 400
        assert (await op.put("/api/auth/password", json={"old_password": "pass1234", "new_password": "newpass1"})).status_code == 200
    finally:
        await op.aclose()
        await view.aclose()

    users = {u["username"]: u for u in (await client.get("/api/users")).json()}
    assert users["op"]["last_login_at"] and users["view"]["role"] == "viewer"
    # 停用账号无法登录；不能删除 / 降级最后一个管理员；不能删除自己
    await client.put(f"/api/users/{users['view']['id']}", json={"username": "view", "role": "viewer", "is_active": False})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.post("/api/auth/login", json={"username": "view", "password": "pass1234"})).status_code == 401
        assert (await anon.post("/api/auth/login", json={"username": "op", "password": "newpass1"})).status_code == 200
    admin_id = users["admin"]["id"]
    r = await client.put(f"/api/users/{admin_id}", json={"username": "admin", "role": "operator"})
    assert r.status_code == 400 and "管理员" in r.json()["detail"]
    assert (await client.delete(f"/api/users/{admin_id}")).status_code == 400

    audit = (await client.get("/api/audit", params={"page_size": 100})).json()["items"]
    actions = [(a["username"], a["action"], a["status_code"]) for a in audit]
    assert ("op", "新建站点", 201) in actions and ("op", "触发采集", 202) in actions
    assert ("op", "修改 AI 设置", 403) in actions and ("op", "修改密码", 200) in actions
    assert ("view", "登录失败", 401) in actions and ("op", "登录", 200) in actions
    assert ("admin", "管理用户", 201) in actions
    assert (await client.get("/api/audit", params={"username": "op", "action": "采集"})).json()["total"] == 1
