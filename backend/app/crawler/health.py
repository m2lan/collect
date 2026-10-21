"""规则健康检查：根据每次采集结果判断规则是否失效，并触发告警 / AI 自动修复。"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawlRun, Source
from app.notify import notify
from app.settings_store import get_setting

log = logging.getLogger(__name__)

PRICE_KEYS = ("current_price", "deal_price", "start_price", "price")


def field_problems(items: list[dict[str, Any]]) -> list[str]:
    """关键字段空值率过高通常意味着页面结构变了。"""
    if not items:
        return []
    problems = []
    n = len(items)
    if "title" in items[0]:
        empty = sum(1 for it in items if not it.get("title"))
        if empty / n > 0.5:
            problems.append(f"标题字段 {empty}/{n} 为空")
    price_keys = [k for k in PRICE_KEYS if k in items[0]]
    if price_keys:
        empty = sum(1 for it in items if all(it.get(k) is None for k in price_keys))
        if empty / n > 0.8:
            problems.append(f"价格字段 {empty}/{n} 为空")
    return problems


async def update_health(
    session: AsyncSession, source: Source, run: CrawlRun, items: list[dict[str, Any]] | None
) -> bool:
    """更新站点健康状态，返回是否新判定为失效（需要修复）。"""
    threshold = int(await get_setting(session, "alert.failure_threshold") or 2)
    failed = run.status == "failed"
    problems = field_problems(items or [])
    previous = source.health

    if failed:
        source.consecutive_failures += 1
        if source.consecutive_failures >= threshold:
            source.health = "broken"
            source.health_message = f"连续 {source.consecutive_failures} 次采集失败：{(run.error or '')[:300]}"
        else:
            source.health = "warning"
            source.health_message = f"最近一次采集失败：{(run.error or '')[:300]}"
    elif problems:
        source.consecutive_failures = 0
        source.health = "warning"
        source.health_message = "；".join(problems)
    else:
        source.consecutive_failures = 0
        source.health, source.health_message = "ok", None

    newly_broken = source.health == "broken" and previous != "broken"
    if newly_broken:
        await notify(session, f"规则失效：{source.name}", f"{source.health_message}\n站点：{source.start_url}")
    elif previous == "broken" and source.health == "ok":
        await notify(session, f"已恢复：{source.name}", "采集恢复正常")
    return newly_broken
