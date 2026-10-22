"""接口嗅探：找出页面加载时返回拍品列表的 JSON 接口，并猜测字段映射，生成 api 模式规则草稿。"""

import json
import re
from typing import Any
from urllib.parse import urlsplit

# 标准字段 → 常见的接口字段名（忽略大小写和下划线）
FIELD_HINTS: dict[str, list[str]] = {
    "title": ["title", "name", "lotname", "goodsname", "itemname", "subject", "itemtitle", "auctiontitle",
              "shortdescription", "assetshortdescription", "lottitle", "productname", "description"],
    "url": ["url", "link", "detailurl", "itemurl", "href", "auctionlink", "permalink"],
    "image": ["image", "img", "pic", "picurl", "cover", "imageurl", "imgurl", "thumbnail", "mainpic", "pictureurl",
              "photo", "photourl", "thumbnailurl"],
    "current_price": ["currentprice", "curprice", "nowprice", "price", "currentbid", "currentamount", "highbid",
                      "currentbidamount", "bidprice"],
    "start_price": ["startprice", "initprice", "openingbid", "beginprice", "startingprice", "displayinitialprice"],
    "deal_price": ["dealprice", "finalprice", "soldprice", "transactionprice", "hammerprice"],
    "bid_count": ["bidcount", "bids", "bidnum", "bidtimes", "offercount", "bidcnt", "numberofbids"],
    "status": ["status", "state", "auctionstatus", "statusname", "statusdesc"],
    "start_time": ["starttime", "begintime", "startdate", "auctionstarttime", "auctionstartdate"],
    "end_time": ["endtime", "enddate", "closetime", "auctionendtime", "deadline", "auctionenddate", "enddateutc",
                 "closedate", "closingdate"],
    "lot_no": ["id", "lotid", "itemid", "lotno", "lotnumber", "goodsid", "assetid"],
}
# 包含匹配（如 assetAuctionEndDateUtc 含 enddate）只用这些足够具体的词
CONTAINS_HINTS: dict[str, list[str]] = {
    "title": ["title", "shortdescription"],
    "image": ["photo", "picture", "image", "thumbnail"],
    "current_price": ["currentbid", "currentprice"],
    "end_time": ["enddate", "endtime", "closedate", "closetime"],
    "start_time": ["startdate", "starttime"],
    "bid_count": ["bidcount", "bidcnt"],
}
TRANSFORMS = {
    "title": ["trim"], "url": ["absolute_url"], "image": ["absolute_url"], "current_price": ["price"],
    "start_price": ["price"], "deal_price": ["price"], "bid_count": ["int"], "status": ["status"],
    "start_time": ["datetime"], "end_time": ["datetime"], "lot_no": [],
}


def _norm(key: str) -> str:
    return re.sub(r"[_\-\s]", "", key).lower()


def _arrays(node: Any, path: str = "$", out: list | None = None, depth: int = 0) -> list[tuple[str, list]]:
    """找出 JSON 中所有“对象数组”及其 JSONPath。"""
    out = [] if out is None else out
    if depth > 8:
        return out
    if isinstance(node, list) and node and all(isinstance(x, dict) for x in node[:5]):
        out.append((f"{path}[*]", node))
        for x in node[:1]:  # 数组里的对象可能还嵌着子数组，只看第一个
            _arrays(x, f"{path}[0]", out, depth + 1)
    elif isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) else f"{path}['{k}']"
            _arrays(v, child, out, depth + 1)
    return out


def _score_array(items: list) -> tuple[int, list[dict[str, Any]]]:
    fields = guess_fields(items[0])
    keys = {f["key"] for f in fields}
    score = 15 * len(fields) + min(len(items), 60)
    if "title" in keys:
        score += 40
    if keys & {"current_price", "start_price", "deal_price"}:
        score += 40
    if len(items) < 2:
        score -= 50
    return score, fields


def guess_fields(sample: dict[str, Any]) -> list[dict[str, Any]]:
    flat: dict[str, str] = {}
    for k, v in sample.items():
        if not isinstance(v, (dict, list)):
            flat.setdefault(_norm(k), k)
        elif isinstance(v, dict):  # 一层嵌套，如 price.current
            for k2, v2 in v.items():
                if not isinstance(v2, (dict, list)):
                    flat.setdefault(_norm(k2), f"{k}.{k2}")
    fields, used = [], set()
    for key, hints in FIELD_HINTS.items():
        path = next((flat[h] for h in hints if h in flat and flat[h] not in used), None)
        if path is None:
            path = next(
                (orig for norm, orig in flat.items() if orig not in used and any(h in norm for h in CONTAINS_HINTS.get(key, []))),
                None,
            )
        if path:
            used.add(path)
            fields.append({"key": key, "type": "jsonpath", "selector": f"$.{path}", "transform": TRANSFORMS[key]})
    return fields


async def can_request_directly(ctx: Any, candidate: dict[str, Any]) -> bool:
    """不经浏览器直接请求接口，能拿到同样结构的数据才适合 api 模式（带签名 / 令牌的接口通常返回 401、403 或报错）。"""
    from app.crawler.extractor import json_find, parse_json_body
    from app.crawler.fetchers import open_fetcher
    from app.schemas.rule import FetchConfig

    if candidate["method"] == "POST" and candidate["body"] is None:
        return False  # 表单编码等无法原样重放的请求体
    try:
        async with open_fetcher(ctx, FetchConfig(mode="static", timeout=20)) as fetcher:
            fetched = await fetcher.fetch(candidate["url"], candidate["method"], candidate["body"])
        found = json_find(parse_json_body(fetched.html), candidate["items_path"])
    except Exception:
        return False
    if len(found) == 1 and isinstance(found[0], list):
        found = found[0]
    return len(found) > 0


def find_candidates(responses: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for r in responses:
        best = None
        for path, items in _arrays(r["data"]):
            score, fields = _score_array(items)
            if best is None or score > best[0]:
                best = (score, path, items, fields)
        if not best or len(best[2]) < 2:
            continue
        score, path, items, fields = best
        body = None
        if r.get("body"):
            try:
                body = json.loads(r["body"])
            except (TypeError, json.JSONDecodeError):
                body = None
        out.append({
            "url": r["url"], "method": r.get("method", "GET"), "body": body, "items_path": path, "count": len(items),
            "sample": items[0], "guessed_fields": fields, "score": score,
        })
    out.sort(key=lambda c: -c["score"])
    seen, unique = set(), []
    for c in out:
        key = (urlsplit(c["url"])._replace(query="").geturl(), c["items_path"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:limit]


def capture_pattern(url: str) -> str:
    """截获接口用的 URL 正则：域名 + 路径（忽略查询参数中的签名、时间戳）。"""
    parts = urlsplit(url)
    return re.escape(f"{parts.netloc}{parts.path}")


def draft_rule(candidate: dict[str, Any], capture: bool = False) -> dict[str, Any]:
    fields = candidate["guessed_fields"] or [{"key": "title", "type": "jsonpath", "selector": "$.title", "transform": ["trim"]}]
    keys = {f["key"] for f in fields}
    method = "POST" if candidate["method"] == "POST" else "GET"
    if capture:
        fetch: dict[str, Any] = {"mode": "browser", "capture": capture_pattern(candidate["url"]), "wait_ms": 1000, "timeout": 45}
    else:
        fetch = {"mode": "api", "url": candidate["url"], "method": method, "body": candidate["body"] if method == "POST" else None}
    return {
        "fetch": fetch,
        "list": {
            "item_selector": candidate["items_path"], "item_selector_type": "jsonpath",
            "unique_key": "url" if "url" in keys else ("lot_no" if "lot_no" in keys else None),
            "fields": fields,
        },
        "pagination": {"type": "none"},
    }
