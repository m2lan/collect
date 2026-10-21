import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["REDIS_URL"] = ""
os.environ["EMBEDDED_SCHEDULER"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-for-unit-tests-only-32b"
if os.path.exists("/opt/pw-browsers/chromium"):
    os.environ.setdefault("BROWSER_EXECUTABLE_PATH", "/opt/pw-browsers/chromium")

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

RULE = {
    "list": {
        "item_selector": ".lot-item",
        "unique_key": "url",
        "fields": [
            {"key": "title", "selector": ".title", "transform": ["trim"]},
            {"key": "url", "selector": "a.link", "attr": "href", "transform": ["absolute_url"]},
            {"key": "current_price", "selector": ".cur-price", "transform": ["price"]},
            {"key": "bid_count", "selector": ".bids", "transform": ["int"]},
            {"key": "status", "selector": ".state", "transform": ["status"]},
            {"key": "end_time", "selector": ".countdown", "attr": "data-end", "transform": ["datetime"]},
            {"key": "image", "selector": "img", "attr": "src", "transform": ["absolute_url"]},
            {"key": "lot_no", "type": "regex", "pattern": r"lot/(\d+)", "from": "url"},
        ],
    },
    "pagination": {"type": "next_link", "selector": "a.next", "max_pages": 5},
}


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # 每个测试使用独立事件循环，连接池不能跨循环复用
    await engine.dispose()
