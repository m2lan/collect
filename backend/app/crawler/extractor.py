import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from parsel import Selector

from app.crawler.transforms import apply_transforms
from app.schemas.rule import FieldRule, Pagination, RuleConfig


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


def _extract_field(item: Selector, rule: FieldRule, values: dict[str, Any]) -> str | None:
    if rule.type == "regex":
        source = values.get(rule.from_) if rule.from_ else _node_value(item, "text")
        if source is None:
            return None
        m = re.search(rule.pattern or "", str(source), re.S)
        if not m:
            return None
        return m.group(1) if m.groups() else m.group(0)

    if not rule.selector:
        node = item
    else:
        found = item.css(rule.selector) if rule.type == "css" else item.xpath(rule.selector)
        if not found:
            return None
        node = found[0]
    return _node_value(node, rule.attr)


def extract_page(html: str, url: str, config: RuleConfig) -> PageResult:
    sel = Selector(text=html)
    lr = config.list
    nodes = sel.css(lr.item_selector) if lr.item_selector_type == "css" else sel.xpath(lr.item_selector)
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for node in nodes:
        values: dict[str, Any] = {}
        for f in lr.fields:
            try:
                raw = _extract_field(node, f, values)
                if raw is None:
                    raw = f.default
                values[f.key] = apply_transforms(raw, f.transform, url)
            except Exception as exc:  # 单个字段出错不影响整条记录
                values[f.key] = None
                if len(errors) < 20:
                    errors.append(f"字段 {f.key}: {exc}")
        items.append(values)
    return PageResult(items=items, next_url=_next_link(sel, url, config.pagination), errors=errors)


def _next_link(sel: Selector, url: str, pagination: Pagination) -> str | None:
    if pagination.type != "next_link" or not pagination.selector:
        return None
    href = sel.css(pagination.selector).attrib.get("href")
    if not href or href.startswith(("javascript:", "#")):
        return None
    return urljoin(url, href)


def page_urls(start_url: str, pagination: Pagination) -> list[str] | None:
    """url_template 翻页可预先算出全部页面；其他方式返回 None，由抓取过程逐页发现。"""
    if pagination.type != "url_template" or not pagination.template:
        return None
    return [
        pagination.template.replace("{page}", str(p))
        for p in range(pagination.start, pagination.start + pagination.max_pages)
    ]
