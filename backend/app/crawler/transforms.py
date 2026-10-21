import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from dateutil import parser as dateparser

from app.schemas.rule import Transform

_CURRENCY = [
    ("US$", "USD"), ("HK$", "HKD"), ("NT$", "TWD"), ("RMB", "CNY"), ("CNY", "CNY"),
    ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("JPY", "JPY"), ("HKD", "HKD"),
    ("¥", "CNY"), ("￥", "CNY"), ("元", "CNY"), ("$", "USD"), ("€", "EUR"), ("£", "GBP"),
    ("円", "JPY"),
]
_UNITS = {"万": Decimal(10_000), "亿": Decimal(100_000_000), "k": Decimal(1_000), "K": Decimal(1_000)}
_NUMBER = re.compile(r"-?\d[\d,，]*(?:\.\d+)?")

_STATUS_WORDS = [
    ("unsold", ("流拍", "撤拍", "中止", "unsold", "passed")),
    ("ended", ("已成交", "成交", "已结束", "结束", "ended", "closed", "sold")),
    ("live", ("进行中", "正在进行", "竞价中", "拍卖中", "live", "bidding", "ongoing")),
    ("upcoming", ("即将开始", "预展", "预告", "未开始", "upcoming", "preview")),
]


class Price(Decimal):
    """携带币种信息的金额。"""

    currency: str | None = None


def parse_price(value: str) -> Price | None:
    match = _NUMBER.search(value)
    if not match:
        return None
    try:
        amount = Decimal(re.sub(r"[,，]", "", match.group()))
    except InvalidOperation:
        return None
    rest = value[match.end():].lstrip()
    if rest[:1] in _UNITS:
        amount *= _UNITS[rest[:1]]
    price = Price(amount)
    upper = value.upper()
    price.currency = next((code for sym, code in _CURRENCY if sym.upper() in upper), None)
    return price


def parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if re.fullmatch(r"\d{10}(\d{3})?", value):
        ts = int(value)
        return datetime.fromtimestamp(ts / 1000 if ts > 10**11 else ts, UTC)
    normalized = re.sub(r"[年月]", "-", value).replace("日", " ").replace("/", "-")
    try:
        return dateparser.parse(normalized)
    except (ValueError, OverflowError):
        return None


def parse_status(value: str) -> str | None:
    lowered = value.lower()
    for status, words in _STATUS_WORDS:
        if any(w in lowered for w in words):
            return status
    return None


def apply_transforms(value: Any, transforms: list[Transform], base_url: str) -> Any:
    for t in transforms:
        if value is None:
            return None
        text = str(value)
        match t.name:
            case "trim":
                value = re.sub(r"\s+", " ", text).strip()
            case "lower":
                value = text.lower()
            case "price":
                value = parse_price(text)
            case "number":
                m = _NUMBER.search(text)
                value = Decimal(re.sub(r"[,，]", "", m.group())) if m else None
            case "int":
                m = re.search(r"\d[\d,，]*", text)
                value = int(re.sub(r"[,，]", "", m.group())) if m else None
            case "datetime":
                value = parse_datetime(text)
            case "absolute_url":
                value = urljoin(base_url, text.strip())
            case "status":
                value = parse_status(text)
            case "regex_replace":
                value = re.sub(t.pattern or "", t.repl, text)
    return value
