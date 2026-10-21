from datetime import UTC, datetime
from decimal import Decimal

from app.crawler.extractor import extract_page, page_urls
from app.crawler.transforms import parse_datetime, parse_price
from app.schemas.rule import Pagination, RuleConfig
from tests.conftest import RULE, fixture_html


def test_parse_price():
    p = parse_price("当前价 ¥12,500.00")
    assert p == Decimal("12500.00") and p.currency == "CNY"
    p = parse_price("3.2万元")
    assert p == Decimal("32000") and p.currency == "CNY"
    assert parse_price("US$800").currency == "USD"
    assert parse_price("暂无") is None


def test_parse_datetime():
    assert parse_datetime("2026年10月20日 18:00") == datetime(2026, 10, 20, 18, 0)
    assert parse_datetime("1793000000000") == datetime.fromtimestamp(1793000000, UTC)


def test_extract_page():
    config = RuleConfig.model_validate(RULE)
    page = extract_page(fixture_html("list_page1.html"), "https://auction.example.com/list", config)
    assert len(page.items) == 2
    first, second = page.items
    assert first["title"] == "清乾隆 青花缠枝莲纹瓶"
    assert first["url"] == "https://auction.example.com/lot/1001"
    assert first["current_price"] == Decimal("12500.00")
    assert first["bid_count"] == 18
    assert first["status"] == "live"
    assert first["image"] == "https://img.example.com/1001.jpg"
    assert first["lot_no"] == "1001"
    assert second["status"] == "ended"
    assert page.next_url == "https://auction.example.com/list?page=2"


def test_url_template_pages():
    p = Pagination(type="url_template", template="https://x.com/list?p={page}", max_pages=3)
    assert page_urls("https://x.com", p) == [f"https://x.com/list?p={i}" for i in (1, 2, 3)]


def test_rule_validation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuleConfig.model_validate({"list": {"item_selector": ".a", "fields": [{"key": "x", "transform": ["eval"]}]}})
    with pytest.raises(ValidationError):
        RuleConfig.model_validate({"list": {"item_selector": ".a", "fields": [{"key": "x", "type": "regex"}]}})
