import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin

from jsonpath_ng.ext import parse as jsonpath_parse
from parsel import Selector

from app.crawler.transforms import apply_transforms
from app.schemas.rule import DetailRule, FieldRule, Pagination, RuleConfig


@dataclass
class PageResult:
    items: list[dict[str, Any]]
    next_url: str | None = None
    errors: list[str] = field(default_factory=list)


def _node_value(node: Selector, attr: str) -> str | None:
    if attr == "text":
        text = " ".join(t.strip() for t in node.xpath(".//text()").getall() if t.strip())
        return text or None
    if attr == "html":
        return node.get()
    return node.attrib.get(attr)


@lru_cache(maxsize=512)
def _jsonpath(expr: str):
    expr = expr.strip()
    if not expr.startswith("$"):
        expr = "$." + expr
    return jsonpath_parse(expr)


def json_find(data: Any, expr: str) -> list[Any]:
    return [m.value for m in _jsonpath(expr).find(data)]


def _regex(rule: FieldRule, source: Any) -> str | None:
    if source is None:
        return None
    m = re.search(rule.pattern or "", str(source), re.S)
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def _extract_field(item: Selector, rule: FieldRule, values: dict[str, Any]) -> str | None:
    if rule.type == "regex":
        source = values.get(rule.from_) if rule.from_ else _node_value(item, "text")
        return _regex(rule, source)

    if not rule.selector:
        node = item
    else:
        found = item.css(rule.selector) if rule.type == "css" else item.xpath(rule.selector)
        if not found:
            return None
        node = found[0]
    return _node_value(node, rule.attr)


def _extract_values(node: Any, fields: list[FieldRule], url: str, errors: list[str], getter) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for f in fields:
        try:
            raw = getter(node, f, values)
            if raw is None:
                raw = f.default
            values[f.key] = apply_transforms(raw, f.transform, url)
        except Exception as exc:  # 单个字段出错不影响整条记录
            values[f.key] = None
            if len(errors) < 20:
                errors.append(f"字段 {f.key}: {exc}")
    return values


def extract_page(html: str, url: str, config: RuleConfig) -> PageResult:
    if config.list.item_selector_type == "jsonpath":
        try:
            data = json.loads(html)
        except json.JSONDecodeError:
            return PageResult(items=[], errors=["接口返回的不是 JSON"])
        return extract_json(data, url, config)
    sel = Selector(text=html)
    lr = config.list
    nodes = sel.css(lr.item_selector) if lr.item_selector_type == "css" else sel.xpath(lr.item_selector)
    errors: list[str] = []
    items = [_extract_values(node, lr.fields, url, errors, _extract_field) for node in nodes]
    return PageResult(items=items, next_url=_next_link(sel, url, config.pagination), errors=errors)


def _json_field(item: Any, rule: FieldRule, values: dict[str, Any]) -> Any:
    if rule.type == "regex":
        source = values.get(rule.from_) if rule.from_ else json.dumps(item, ensure_ascii=False)
        return _regex(rule, source)
    if not rule.selector:
        return item if not isinstance(item, (dict, list)) else json.dumps(item, ensure_ascii=False)
    found = json_find(item, rule.selector)
    if not found or found[0] is None:
        return None
    value = found[0]
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def extract_json(data: Any, url: str, config: RuleConfig) -> PageResult:
    """api 模式：item_selector 为 JSONPath（如 $.data.list[*]），字段 selector 为相对拍品的 JSONPath。"""
    found = json_find(data, config.list.item_selector)
    # $.data.list 这种写法匹配到的是数组本身，展开为拍品
    if len(found) == 1 and isinstance(found[0], list):
        found = found[0]
    errors: list[str] = []
    items = [_extract_values(it, config.list.fields, url, errors, _json_field) for it in found if it is not None]
    return PageResult(items=items, errors=errors)


def extract_detail(html: str, url: str, detail: DetailRule) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    root = Selector(text=html)
    return _extract_values(root, detail.fields, url, errors, _extract_field), errors


def _next_link(sel: Selector, url: str, pagination: Pagination) -> str | None:
    if pagination.type != "next_link" or not pagination.selector:
        return None
    href = sel.css(pagination.selector).attrib.get("href")
    if not href or href.startswith(("javascript:", "#")):
        return None
    return urljoin(url, href)


def page_urls(start_url: str, pagination: Pagination, method: str = "GET") -> list[str] | None:
    """url_template / page_param(GET) 翻页可预先算出全部页面；其他方式返回 None，由抓取过程逐页发现。"""
    pages = range(pagination.start, pagination.start + pagination.max_pages)
    if pagination.type == "url_template" and pagination.template:
        return [pagination.template.replace("{page}", str(p)) for p in pages]
    if pagination.type == "page_param" and pagination.param and method == "GET":
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(start_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        return [urlunsplit(parts._replace(query=urlencode({**query, pagination.param: p}))) for p in pages]
    return None
