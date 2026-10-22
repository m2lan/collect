import gzip

from app.crawler.errors import classify_error
from tests.conftest import RULE
from tests.test_api import client, server, wait_run  # noqa: F401


def test_classify_error():
    assert classify_error("LoginExpired: 登录已失效") == "login"
    assert classify_error("RuntimeError: HTTP 403: https://x") == "blocked"
    assert classify_error("TimeoutError: Page.goto: Timeout 30000ms exceeded") == "timeout"
    assert classify_error("ConnectError: [Errno 111] Connection refused") == "network"
    assert classify_error("未抽取到任何拍品，请检查规则或登录状态") == "structure"
    assert classify_error("ValueError: boom") == "other" and classify_error(None) is None


async def test_dashboard(client, server):
    ok = (await client.post("/api/sources", json={"name": "好站", "start_url": f"{server}/list", "rate_limit": 20, "rule": RULE})).json()
    bad_rule = {**RULE, "list": {**RULE["list"], "item_selector": ".gone"}}
    bad = (await client.post("/api/sources", json={"name": "坏站", "start_url": f"{server}/list", "rate_limit": 20, "rule": bad_rule})).json()
    for sid in (ok["id"], bad["id"]):
        await wait_run(client, (await client.post(f"/api/sources/{sid}/crawl")).json()["id"])

    d = (await client.get("/api/dashboard", params={"days": 7})).json()
    t = d["totals"]
    assert t["sources"] == 2 and t["lots"] == 3 and t["runs_24h"] == 2 and t["success_rate_24h"] == 0.5
    assert t["new_lots_24h"] == 3 and t["avg_duration_ms_24h"] >= 0
    assert len(d["daily"]) == 7 and d["daily"][-1]["success"] == 1 and d["daily"][-1]["failed"] == 1
    assert d["failure_reasons"] == [{"type": "structure", "label": "页面结构变化", "count": 1}]
    by_name = {s["name"]: s for s in d["sources"]}
    assert by_name["好站"]["success_rate"] == 1 and by_name["坏站"]["success_rate"] == 0 and by_name["好站"]["lots"] == 3
    assert d["recent_failures"][0]["source_name"] == "坏站"
    run = (await client.get("/api/runs", params={"source_id": bad["id"]})).json()["items"][0]
    assert run["error_type"] == "structure" and run["duration_ms"] is not None


async def test_backups_api(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path / "missing"))
    assert (await client.get("/api/backups")).json()["enabled"] is False
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    (tmp_path / "collect_20261022_033000.sql.gz").write_bytes(gzip.compress(b"-- dump"))
    (tmp_path / ".status.json").write_text('{"ok": true, "file": "collect_20261022_033000.sql.gz"}')
    r = (await client.get("/api/backups")).json()
    assert r["enabled"] and r["files"][0]["name"] == "collect_20261022_033000.sql.gz" and r["status"]["ok"]
    assert (await client.post("/api/backups")).status_code == 202 and (tmp_path / ".request").exists()
    assert (await client.get("/api/backups")).json()["pending"] is True
    dl = await client.get("/api/backups/collect_20261022_033000.sql.gz")
    assert gzip.decompress(dl.content) == b"-- dump"
    assert (await client.get("/api/backups/..%2F.status.json")).status_code == 404
