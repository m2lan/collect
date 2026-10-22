from datetime import UTC, datetime
from zoneinfo import ZoneInfo
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
    sh = ZoneInfo("Asia/Shanghai")
    assert parse_datetime("2026年10月20日 18:00") == datetime(2026, 10, 20, 18, 0, tzinfo=sh)
    assert parse_datetime("1793000000000") == datetime.fromtimestamp(1793000000, UTC)
    assert parse_datetime("2026-10-21T21:48:00Z") == datetime(2026, 10, 21, 21, 48, tzinfo=UTC)
    now = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # 北京时间 10-22 11:00
    assert parse_datetime("预计今天10:00结束", now) == datetime(2026, 10, 22, 10, 0, tzinfo=sh)
    assert parse_datetime("明天 09:30", now) == datetime(2026, 10, 23, 9, 30, tzinfo=sh)
    assert parse_datetime("11月05日10:00", now) == datetime(2026, 11, 5, 10, 0, tzinfo=sh)
    # 12 月看到 "01月05日" 指的是明年
    assert parse_datetime("01月05日10:00", datetime(2026, 12, 20, tzinfo=UTC)) == datetime(2027, 1, 5, 10, 0, tzinfo=sh)


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


def test_captured_json_template_and_map():
    from app.crawler.extractor import extract_captured, parse_json_body

    rule = RuleConfig.model_validate({
        "fetch": {"mode": "browser", "capture": r"auctionwalle", "capture_body": "getPageModulesData"},
        "list": {
            "item_selector": "$.data.data.GQL_getPageModulesData.*.items.schemeList[*]",
            "item_selector_type": "jsonpath", "unique_key": "lot_no",
            "fields": [
                {"key": "title", "type": "jsonpath", "selector": "$.auctionTitle", "transform": ["trim"]},
                {"key": "current_price", "type": "template", "selector": "{price}{priceUnit}", "transform": ["price"]},
                {"key": "url", "type": "template", "selector": "https:{auctionLink}"},
                {"key": "status", "type": "jsonpath", "selector": "$.status",
                 "transform": [{"name": "map", "mapping": {"ing": "live", "before": "upcoming", "after": "ended"}}]},
                {"key": "lot_no", "type": "jsonpath", "selector": "$.itemId"},
            ],
        },
    })
    body = 'mtopjsonp3({"data": {"data": {"GQL_getPageModulesData": {"200": {"items": {"schemeList": [' \
        '{"itemId": 1, "auctionTitle": " 翡翠 ", "price": "1.8", "priceUnit": "万", "status": "ing", "auctionLink": "//zc-item.taobao.com/auction/1.htm"},' \
        '{"itemId": 2, "auctionTitle": "玉牌", "price": 300, "priceUnit": "", "status": "before", "auctionLink": "//zc-item.taobao.com/auction/2.htm"}' \
        ']}}, "100": {"nav": []}}}}})'
    responses = [
        {"url": "https://h5api.m.taobao.com/h5/mtop.taobao.datafront.invoke.auctionwalle/1.0/", "body": "data=getPageModulesData", "data": parse_json_body(body)},
        {"url": "https://h5api.m.taobao.com/h5/mtop.user.getusersimple/1.0/", "body": "", "data": {"x": [1]}},
    ]
    page = extract_captured(responses, "https://zc-paimai.taobao.com/", rule)
    assert [it["current_price"] for it in page.items] == [Decimal("18000"), Decimal("300")]
    assert page.items[0]["title"] == "翡翠" and page.items[0]["status"] == "live" and page.items[1]["status"] == "upcoming"
    assert page.items[0]["url"] == "https://zc-item.taobao.com/auction/1.htm"
    assert "没有截获" in extract_captured(responses[1:], "x", rule).errors[0]


def test_user_agents_are_chromium():
    from app.crawler.fetchers.base import USER_AGENTS

    assert all("Chrome/" in ua and "Firefox" not in ua for ua in USER_AGENTS)
