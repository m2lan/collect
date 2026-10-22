from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.analytics import market_analysis, render_report
from app.api.deps import SessionDep
from app.config import get_settings
from app.notify import notify
from app.search import parse_synonyms
from app.settings_store import get_setting

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _analyze(session, days, source_id, category, keyword, period):
    synonyms = parse_synonyms(await get_setting(session, "search.synonyms"))
    return await market_analysis(session, days=days, source_id=source_id, category=category, keyword=keyword,
                                 synonyms=synonyms, period=period)


@router.get("")
async def analytics(
    session: SessionDep, days: int = Query(90, ge=1, le=3650), source_id: int | None = None,
    category: str | None = None, keyword: str | None = None, period: Literal["week", "month"] = "week",
) -> dict:
    return await _analyze(session, days, source_id, category, keyword, period)


class ReportIn(BaseModel):
    period: Literal["week", "month"] = "week"
    source_id: int | None = None
    category: str | None = None
    keyword: str | None = None
    push: bool = False


@router.post("/report")
async def report(body: ReportIn, session: SessionDep) -> dict:
    """生成周报 / 月报（Markdown），可推送到告警通知渠道。"""
    days = 7 if body.period == "week" else 30
    data = await _analyze(session, days, body.source_id, body.category, body.keyword,
                          "week" if body.period == "month" else "week")
    now = datetime.now(ZoneInfo(get_settings().timezone))
    scope = " · ".join(x for x in (body.category, body.keyword) if x)
    title = f"拍卖行情{'周' if body.period == 'week' else '月'}报 {now:%Y-%m-%d}" + (f"（{scope}）" if scope else "")
    text = render_report(data, title)
    if body.push:
        await notify(session, title, text)
    return {"title": title, "markdown": text, "data": data}
