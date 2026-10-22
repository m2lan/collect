"""规则失效后的 AI 自动修复：重新生成规则，试跑通过即启用新版本（无审核环节）。"""

import logging

from fastapi import HTTPException
from sqlalchemy import select

from app.ai import service
from app.crawler.runner import apply_network, context_for
from app.db import SessionLocal
from app.models import Rule, Source
from app.notify import notify
from app.settings_store import get_setting

log = logging.getLogger(__name__)


async def auto_repair(source_id: int) -> int | None:
    """返回新启用的规则版本号；未执行或失败返回 None。"""
    async with SessionLocal() as session:
        source = await session.get(Source, source_id)
        if source is None or not source.allow_ai_repair or not await get_setting(session, "ai.auto_repair"):
            return None
        try:
            await service.ensure_ai_available(session)
        except HTTPException as exc:
            log.info("auto repair skipped: %s", exc.detail)
            return None
        old = await session.scalar(select(Rule).where(Rule.source_id == source_id, Rule.is_active))
        ctx = await apply_network(session, context_for(source), source.use_proxy)
        mode = ((old.config.get("fetch") or {}).get("mode") if old else None) or source.fetch_mode
        try:
            gen, result = await service.run_generation(
                session, ctx, mode, source_id=source_id, trigger="repair", previous_rule=old.config if old else None,
                analyze_detail=bool(old and (old.config.get("detail") or {}).get("enabled")),
            )
        except HTTPException as exc:
            await notify(session, f"AI 自动修复失败：{source.name}", str(exc.detail))
            return None
        if not result.ok:
            await notify(session, f"AI 自动修复失败：{source.name}", "；".join(result.problems)[:500])
            return None
        rule = await service.activate_ai_rule(session, source_id, gen, note="规则失效后 AI 自动修复")
        source = await session.get(Source, source_id)
        assert source
        source.health, source.health_message, source.consecutive_failures = "warning", f"已由 AI 自动修复为 v{rule.version}，等待下次采集验证", 0
        await session.commit()
        await notify(session, f"AI 已自动修复规则：{source.name}", f"新规则 v{rule.version}，试跑抽取 {len(result.items)} 条")
        return rule.version
