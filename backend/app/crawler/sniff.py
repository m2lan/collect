"""接口嗅探：找出页面加载时返回拍品列表的 JSON 接口，并猜测字段映射，生成 api 模式规则草稿。"""

import json
import re
from typing import Any
from urllib.parse import urlsplit

# 标准字段 → 常见的接口字段名（忽略大小写和下划线）
FIELD_HINTS: dict[str, list[str]] = {
    "title": ["title", "name", "lotname", "goodsname", "itemname", "subject", "itemtitle"],
    "url": ["url", "link", "detailurl", "itemurl", "href"],
    "image": ["image", "img", "pic", "picurl", "cover", "imageurl", "imgurl", "thumbnail", "mainpic"],
    "current_price": ["currentprice", "curprice", "nowprice", "price", "currentbid", "currentamount"],
    "start_price": ["startprice", "initprice", "openingbid", "beginprice", "startingprice"],
    "deal_price": ["dealprice", "finalprice", "soldprice", "transactionprice", "hammerprice"],
    "bid_count": ["bidcount", "bids", "bidnum", "bidtimes", "offercount", "applycount"],
    "status": ["status", "state", "auctionstatus", "statusname", "statusdesc"],
    "start_time": ["starttime", "begintime", "startdate", "auctionstarttime"],
    "end_time": ["endtime", "enddate", "closetime", "auctionendtime", "deadline"],
    "lot_no": ["id", "lotid", "itemid", "lotno", "lotnumber", "goodsid"],
}
TRANSFORMS = {
    "title": ["trim"], "url": ["absolute_url"], "image": ["absolute_url"], "current_price": ["price"],
    "start_price": ["price"], "deal_price": ["price"], "bid_count": ["int"], "status": ["status"],
    "start_time": ["datetime"], "end_time": ["datetime"], "lot_no": [],
}


def _norm(key: str) -> str:
    return re.sub(r"[_\-\s]", "", key).lower()


def _best_array(node: Any, path: str = "$") -> tuple[int, str, list] | None:
    best = None
    if isinstance(node, list) and node and all(isinstance(x, dict) for x in node[:5]):
        best = (len(node), f"{path}[*]", node)
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) else f"{path}['{k}']"
            found = _best_array(v, child)
            if found and (best is None or found[0] > best[0]):
                best = found
    return best


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
        for h in hints:
            path = flat.get(h)
            if path and path not in used:
                used.add(path)
                fields.append({"key": key, "type": "jsonpath", "selector": f"$.{path}", "transform": TRANSFORMS[key]})
                break
    return fields


def find_candidates(responses: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for r in responses:
        found = _best_array(r["data"])
        if not found or found[0] < 2:
            continue
        count, path, items = found
        body = None
        if r.get("body"):
            try:
                body = json.loads(r["body"])
            except (TypeError, json.JSONDecodeError):
                body = None
        fields = guess_fields(items[0])
        out.append({
            "url": r["url"], "method": r.get("method", "GET"), "body": body, "items_path": path, "count": count,
            "sample": items[0], "guessed_fields": fields,
            "score": count + 10 * len(fields),
        })
    out.sort(key=lambda c: -c["score"])
    seen, unique = set(), []
    for c in out:
        key = (urlsplit(c["url"])._replace(query="").geturl(), c["items_path"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:limit]


def draft_rule(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = candidate["guessed_fields"] or [{"key": "title", "type": "jsonpath", "selector": "$.title", "transform": ["trim"]}]
    keys = {f["key"] for f in fields}
    method = "POST" if candidate["method"] == "POST" else "GET"
    return {
        "fetch": {"mode": "api", "url": candidate["url"], "method": method, "body": candidate["body"] if method == "POST" else None},
        "list": {
            "item_selector": candidate["items_path"], "item_selector_type": "jsonpath",
            "unique_key": "url" if "url" in keys else ("lot_no" if "lot_no" in keys else None),
            "fields": fields,
        },
        "pagination": {"type": "none"},
    }
