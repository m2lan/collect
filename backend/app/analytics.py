"""行情分析与同款比价。"""

import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Lot, Source
from app.models.entities import utcnow
from app.search import search_condition

_STOP = set("的了和与及等件套个只一二三四五六七八九十")


def _grams(text: str) -> set[str]:
    """中文按字二元组、英文按单词切分，用于标题相似度。"""
    text = re.sub(r"[（）()【】\[\]《》“”\"'，,。.:：;；!！?？/\\\-_+*#·]", " ", (text or "").lower())
    out: set[str] = set()
    for chunk in text.split():
        if re.fullmatch(r"[a-z0-9]+", chunk):
            if len(chunk) > 1:
                out.add(chunk)
            continue
        chars = [c for c in chunk if c not in _STOP]
        out |= {a + b for a, b in zip(chars, chars[1:], strict=False)}
        if len(chars) == 1:
            out.add(chars[0])
    return out


def similarity(a: Lot, b: Lot) -> float:
    ga, gb = _grams(a.title or ""), _grams(b.title or "")
    score = len(ga & gb) / len(ga | gb) if ga and gb else 0.0
    ta, tb = a.tags or {}, b.tags or {}
    if ta.get("category") and ta.get("category") == tb.get("category"):
        score += 0.1
    for k in ("sub_category", "era", "material", "brand"):
        if ta.get(k) and ta.get(k) == tb.get(k):
            score += 0.08
    return min(score, 1.0)


def price_of(lot: Lot) -> Decimal | None:
    return lot.deal_price if lot.deal_price is not None else lot.current_price


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None, "avg": None}
    v = sorted(values)
    q = statistics.quantiles(v, n=4) if len(v) >= 2 else [v[0]] * 3
    return {"min": v[0], "p25": q[0], "median": statistics.median(v), "p75": q[2], "max": v[-1],
            "avg": round(sum(v) / len(v), 2)}


async def similar_lots(session: AsyncSession, lot: Lot, limit: int = 10, min_score: float = 0.2) -> dict[str, Any]:
    stmt = select(Lot).where(Lot.id != lot.id)
    category = (lot.tags or {}).get("category")
    if category and category != "其他":
        stmt = stmt.where(Lot.tags["category"].as_string() == category)
    else:
        grams = sorted(_grams(lot.title or ""), key=len, reverse=True)[:6]
        if not grams:
            return {"items": [], "stats": {**_quantiles([]), "count": 0, "own_vs_median": None}}
        stmt = stmt.where(or_(*(Lot.title.ilike(f"%{g}%") for g in grams)))
    candidates = (await session.scalars(stmt.order_by(Lot.last_seen_at.desc()).limit(3000))).all()
    scored = sorted(((similarity(lot, c), c) for c in candidates), key=lambda x: -x[0])
    top = [(s, c) for s, c in scored if s >= min_score][:limit]
    names = dict((await session.execute(select(Source.id, Source.name))).all())
    prices = [float(p) for _, c in top if (p := price_of(c)) is not None and (lot.currency or "CNY") == (c.currency or "CNY")]
    own = price_of(lot)
    stats = _quantiles(prices)
    stats["count"] = len(prices)
    stats["own_vs_median"] = round(float(own) / stats["median"] - 1, 4) if own is not None and stats["median"] else None
    return {
        "items": [
            {
                "id": c.id, "title": c.title, "source_id": c.source_id, "source_name": names.get(c.source_id),
                "price": price_of(c), "currency": c.currency, "status": c.status, "end_time": c.end_time,
                "url": c.url, "image": c.image, "similarity": round(s, 3), "same_source": c.source_id == lot.source_id,
            }
            for s, c in top
        ],
        "stats": stats,
    }


async def market_analysis(
    session: AsyncSession, *, days: int = 90, source_id: int | None = None, category: str | None = None,
    keyword: str | None = None, synonyms: list[set[str]] | None = None, period: str = "week",
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    stmt = select(Lot).where(Lot.last_seen_at >= since)
    if source_id:
        stmt = stmt.where(Lot.source_id == source_id)
    if category:
        stmt = stmt.where(Lot.tags["category"].as_string() == category)
    if keyword:
        cond = search_condition(keyword, synonyms)
        if cond is not None:
            stmt = stmt.where(cond)
    lots = (await session.scalars(stmt.limit(100_000))).all()
    names = dict((await session.execute(select(Source.id, Source.name))).all())
    tz = ZoneInfo(get_settings().timezone)

    closed = [l for l in lots if l.status in ("ended", "unsold")]
    sold = [l for l in closed if l.status == "ended"]
    deal_prices = [float(p) for l in sold if (p := price_of(l)) is not None]
    premiums = [
        float(price_of(l)) / float(l.start_price) - 1
        for l in sold if price_of(l) is not None and l.start_price
    ]

    def bucket(dt: datetime) -> str:
        d = dt.astimezone(tz)
        if period == "month":
            return d.strftime("%Y-%m")
        start = d - timedelta(days=d.weekday())
        return start.strftime("%Y-%m-%d")

    trend: dict[str, dict[str, Any]] = defaultdict(lambda: {"lots": 0, "sold": 0, "closed": 0, "prices": []})
    for l in lots:
        t = trend[bucket(l.end_time or l.last_seen_at)]
        t["lots"] += 1
        if l.status in ("ended", "unsold"):
            t["closed"] += 1
        if l.status == "ended":
            t["sold"] += 1
            if (p := price_of(l)) is not None:
                t["prices"].append(float(p))

    def group(key) -> list[dict[str, Any]]:
        g: dict[str, list[Lot]] = defaultdict(list)
        for l in lots:
            g[key(l) or "未分类"].append(l)
        out = []
        for k, items in g.items():
            c = [x for x in items if x.status in ("ended", "unsold")]
            s = [x for x in c if x.status == "ended"]
            ps = [float(p) for x in s if (p := price_of(x)) is not None]
            out.append({"name": k, "lots": len(items), "closed": len(c), "sold": len(s),
                        "sold_rate": round(len(s) / len(c), 4) if c else None,
                        "median_price": statistics.median(ps) if ps else None,
                        "live": sum(1 for x in items if x.status == "live")})
        return sorted(out, key=lambda x: -x["lots"])

    return {
        "summary": {
            "lots": len(lots), "live": sum(1 for l in lots if l.status == "live"),
            "upcoming": sum(1 for l in lots if l.status == "upcoming"),
            "closed": len(closed), "sold": len(sold),
            "sold_rate": round(len(sold) / len(closed), 4) if closed else None,
            "premium_avg": round(statistics.mean(premiums), 4) if premiums else None,
            "premium_median": round(statistics.median(premiums), 4) if premiums else None,
            "deal_price": _quantiles(deal_prices),
        },
        "trend": [
            {"period": k, "lots": v["lots"], "sold": v["sold"], "closed": v["closed"],
             "sold_rate": round(v["sold"] / v["closed"], 4) if v["closed"] else None,
             "median_price": statistics.median(v["prices"]) if v["prices"] else None}
            for k, v in sorted(trend.items())
        ],
        "by_category": group(lambda l: (l.tags or {}).get("category")),
        "by_source": group(lambda l: names.get(l.source_id)),
    }


def render_report(data: dict[str, Any], title: str) -> str:
    s = data["summary"]
    pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    money = lambda v: "-" if v is None else f"{v:,.0f}"  # noqa: E731
    lines = [
        f"# {title}", "",
        f"- 拍品 {s['lots']} 件：进行中 {s['live']}，即将开始 {s['upcoming']}，已结束 {s['closed']}（成交 {s['sold']}）",
        f"- 成交率 {pct(s['sold_rate'])}；成交价相对起拍价溢价：平均 {pct(s['premium_avg'])}，中位 {pct(s['premium_median'])}",
        f"- 成交价：中位 {money(s['deal_price']['median'])}，四分位 {money(s['deal_price']['p25'])} ~ {money(s['deal_price']['p75'])}，"
        f"最高 {money(s['deal_price']['max'])}",
        "", "## 分品类",
    ]
    for c in data["by_category"][:10]:
        lines.append(f"- {c['name']}：{c['lots']} 件，成交率 {pct(c['sold_rate'])}，成交中位价 {money(c['median_price'])}")
    lines += ["", "## 分站点"]
    for c in data["by_source"][:10]:
        lines.append(f"- {c['name']}：{c['lots']} 件，进行中 {c['live']}，成交率 {pct(c['sold_rate'])}")
    if data["trend"]:
        lines += ["", "## 走势"]
        for t in data["trend"][-8:]:
            lines.append(f"- {t['period']}：{t['lots']} 件，成交 {t['sold']}，成交中位价 {money(t['median_price'])}")
    return "\n".join(lines)
