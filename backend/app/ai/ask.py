"""自然语言查数据：AI 把问题转成结构化查询计划（不生成 SQL），后端安全执行并给出图表与回答。"""

import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.providers import LLMError, Message
from app.ai.tagging import CATEGORIES
from app.config import get_settings
from app.models import AIGeneration, Lot, Source
from app.models.entities import utcnow
from app.search import parse_synonyms, search_condition
from app.settings_store import get_setting

METRICS = {
    "count": "数量", "avg_price": "平均价", "median_price": "中位价", "max_price": "最高价", "min_price": "最低价",
    "sum_price": "总金额", "sold_rate": "成交率", "premium": "平均溢价率",
}
GROUPS = {"none": "", "category": "品类", "era": "年代", "source": "站点", "status": "状态",
          "day": "日期", "week": "周", "month": "月份"}
STATUS_LABEL = {"upcoming": "即将开始", "live": "进行中", "ended": "已成交", "unsold": "流拍"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["aggregate", "list"], "description": "aggregate 统计；list 列出拍品"},
        "filters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "标题关键词，多个用空格分隔（AND）"},
                "category": {"type": "string", "enum": [""] + CATEGORIES},
                "era": {"type": "string"},
                "source": {"type": "string", "description": "站点名称（模糊匹配）"},
                "status": {"type": "string", "enum": ["", "upcoming", "live", "ended", "unsold"],
                           "description": "ended=已成交，unsold=流拍；问成交价时用 ended"},
                "min_price": {"type": "number"}, "max_price": {"type": "number"},
                "days": {"type": "integer", "description": "最近多少天（按结束时间，无结束时间按采集时间），0 表示不限"},
                "ending_within_hours": {"type": "integer", "description": "未来多少小时内结束，0 表示不限"},
            },
        },
        "metric": {"type": "string", "enum": list(METRICS)},
        "group_by": {"type": "string", "enum": list(GROUPS)},
        "sort": {"type": "string", "enum": ["-price", "price", "end_time", "-end_time", "-last_seen_at", "-value", "value"]},
        "limit": {"type": "integer", "description": "返回条数，1-50"},
        "chart": {"type": "string", "enum": ["number", "bar", "line", "pie", "table"]},
        "title": {"type": "string", "description": "结果标题"},
    },
    "required": ["mode", "filters", "chart", "title"],
}


async def _system_prompt(session: AsyncSession) -> str:
    sources = (await session.scalars(select(Source.name))).all()
    today = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%d")
    return (
        "你是拍卖数据分析助手。把管理员的问题转换为查询计划（调用 make_plan），不要编造数据。\n"
        f"今天是 {today}。已有站点：{'、'.join(sources) or '无'}。品类取值：{'、'.join(CATEGORIES)}。\n"
        "规则：问“多少件/几个”用 count；问价格用 avg_price/median_price/max_price/min_price；问“成交价”需 status=ended；"
        "问趋势/走势用 group_by=week 或 month 且 chart=line；按品类/站点对比用 bar（占比用 pie）；"
        "问“哪些/列出/最贵的”用 mode=list 并给 sort 和 limit，chart=table；单个数字用 chart=number。"
        "没有提到时间范围时 days=0。"
    )


def _price(l: Lot) -> float | None:
    p = l.deal_price if l.deal_price is not None else l.current_price
    return float(p) if p is not None else None


def _metric(metric: str, lots: list[Lot]) -> float | None:
    prices = [p for l in lots if (p := _price(l)) is not None]
    if metric == "count":
        return float(len(lots))
    if metric == "sold_rate":
        closed = [l for l in lots if l.status in ("ended", "unsold")]
        return round(sum(1 for l in closed if l.status == "ended") / len(closed), 4) if closed else None
    if metric == "premium":
        ps = [_price(l) / float(l.start_price) - 1 for l in lots if l.status == "ended" and _price(l) and l.start_price]
        return round(statistics.mean(ps), 4) if ps else None
    if not prices:
        return None
    return round({
        "avg_price": statistics.mean, "median_price": statistics.median, "max_price": max, "min_price": min, "sum_price": sum,
    }[metric](prices), 2)


async def execute_plan(session: AsyncSession, plan: dict[str, Any]) -> dict[str, Any]:
    f = plan.get("filters") or {}
    stmt = select(Lot)
    now = utcnow()
    if f.get("keyword"):
        cond = search_condition(str(f["keyword"]), parse_synonyms(await get_setting(session, "search.synonyms")))
        if cond is not None:
            stmt = stmt.where(cond)
    if f.get("category"):
        stmt = stmt.where(Lot.tags["category"].as_string() == f["category"])
    if f.get("era"):
        stmt = stmt.where(Lot.tags["era"].as_string().ilike(f"%{f['era']}%"))
    if f.get("status"):
        stmt = stmt.where(Lot.status == f["status"])
    if f.get("source"):
        ids = (await session.scalars(select(Source.id).where(Source.name.ilike(f"%{f['source']}%")))).all()
        stmt = stmt.where(Lot.source_id.in_(ids or [-1]))
    if f.get("days"):
        since = now - timedelta(days=int(f["days"]))
        stmt = stmt.where(or_(Lot.end_time >= since, (Lot.end_time.is_(None)) & (Lot.last_seen_at >= since)))
    if f.get("ending_within_hours"):
        stmt = stmt.where(Lot.end_time > now, Lot.end_time <= now + timedelta(hours=int(f["ending_within_hours"])))
    lots = list((await session.scalars(stmt.limit(50_000))).all())
    lo, hi = f.get("min_price"), f.get("max_price")
    if lo is not None or hi is not None:
        lots = [l for l in lots if (p := _price(l)) is not None and (lo is None or p >= lo) and (hi is None or p <= hi)]
    names = dict((await session.execute(select(Source.id, Source.name))).all())
    limit = max(1, min(int(plan.get("limit") or 20), 50))

    if plan.get("mode") == "list":
        key = plan.get("sort") or "-price"
        desc = key.startswith("-")
        field = key.lstrip("-")
        def sort_value(l: Lot) -> float | None:
            v = _price(l) if field in ("price", "value") else getattr(l, field, None)
            return v.timestamp() if isinstance(v, datetime) else v

        # 有值的按方向排序，没有值的排在最后
        present = [l for l in lots if sort_value(l) is not None]
        present.sort(key=sort_value, reverse=desc)  # type: ignore[arg-type,return-value]
        lots = present + [l for l in lots if sort_value(l) is None]
        rows = [{"id": l.id, "title": l.title, "source": names.get(l.source_id), "price": _price(l), "currency": l.currency,
                 "status": STATUS_LABEL.get(l.status or "", l.status), "end_time": l.end_time, "url": l.url}
                for l in lots[:limit]]
        return {"columns": ["title", "source", "price", "status", "end_time"], "rows": rows, "total": len(lots)}

    metric = plan.get("metric") or "count"
    group = plan.get("group_by") or "none"
    if group == "none":
        return {"columns": ["value"], "rows": [{"label": METRICS[metric], "value": _metric(metric, lots)}], "total": len(lots)}
    tz = ZoneInfo(get_settings().timezone)

    def key_of(l: Lot) -> str:
        if group in ("category", "era"):
            return (l.tags or {}).get(group) or "未分类"
        if group == "source":
            return names.get(l.source_id) or "-"
        if group == "status":
            return STATUS_LABEL.get(l.status or "", l.status or "未知")
        d = (l.end_time or l.last_seen_at).astimezone(tz)
        if group == "day":
            return d.strftime("%Y-%m-%d")
        if group == "week":
            return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        return d.strftime("%Y-%m")

    buckets: dict[str, list[Lot]] = defaultdict(list)
    for l in lots:
        buckets[key_of(l)].append(l)
    rows = [{"label": k, "value": _metric(metric, v), "count": len(v)} for k, v in buckets.items()]
    if group in ("day", "week", "month"):
        rows.sort(key=lambda r: r["label"])
    else:
        rows.sort(key=lambda r: -(r["value"] or 0))
        rows = rows[:limit]
    return {"columns": ["label", "value"], "rows": rows, "total": len(lots)}


def fallback_answer(plan: dict[str, Any], result: dict[str, Any]) -> str:
    rows = result["rows"]
    if not rows or result["total"] == 0:
        return "没有找到符合条件的拍品。"
    if plan.get("mode") == "list":
        return f"共找到 {result['total']} 件，列出前 {len(rows)} 件。"
    metric = METRICS.get(plan.get("metric") or "count", "")
    if len(rows) == 1 and "label" in rows[0] and plan.get("group_by", "none") == "none":
        v = rows[0]["value"]
        return f"{metric}：{'-' if v is None else (f'{v * 100:.1f}%' if plan.get('metric') in ('sold_rate', 'premium') else f'{v:,.2f}'.rstrip('0').rstrip('.'))}（共 {result['total']} 件）"
    top = max(rows, key=lambda r: r["value"] or 0)
    return f"按{GROUPS.get(plan.get('group_by'), '')}统计{metric}，共 {len(rows)} 组，最高为「{top['label']}」。"


async def ask(session: AsyncSession, question: str) -> dict[str, Any]:
    await service.ensure_ai_available(session)
    row, provider = await service.resolve_provider(session)
    started = time.monotonic()
    gen = AIGeneration(provider_id=row.id, trigger="ask", url=question[:500], user_hint=question, status="running")
    session.add(gen)
    await session.commit()
    try:
        res = await provider.generate_structured(await _system_prompt(session), [Message("user", question)],
                                                 PLAN_SCHEMA, "make_plan", "提交查询计划")
        plan = res.data
        usage = [res.prompt_tokens, res.completion_tokens]
        result = await execute_plan(session, plan)
        answer = fallback_answer(plan, result)
        try:  # 让 AI 用一两句话回答问题（失败则用模板回答）
            summary = await provider.generate_structured(
                "你是拍卖数据分析助手。根据查询结果，用一两句中文直接回答问题，给出关键数字，不要编造结果中没有的数据。",
                [Message("user", json.dumps({"question": question, "plan": plan, "total": result["total"],
                                             "rows": result["rows"][:20]}, ensure_ascii=False, default=str))],
                {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
                "answer", "提交回答",
            )
            answer = str(summary.data.get("answer") or answer)
            usage[0] += summary.prompt_tokens
            usage[1] += summary.completion_tokens
        except LLMError:
            pass
    except LLMError as exc:
        gen.status, gen.error = "failed", str(exc)[:2000]
        await session.commit()
        raise
    gen.status, gen.item_count = "success", result["total"]
    gen.explanation = answer[:2000]
    gen.prompt_tokens, gen.completion_tokens = usage
    gen.duration_ms = int((time.monotonic() - started) * 1000)
    await session.commit()
    return {"question": question, "plan": plan, "answer": answer, **result,
            "metric_label": METRICS.get(plan.get("metric") or "count"), "group_label": GROUPS.get(plan.get("group_by") or "none")}
