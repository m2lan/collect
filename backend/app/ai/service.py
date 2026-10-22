from datetime import UTC, datetime, time
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import LLMError, LLMProvider, ProviderConfig, build_provider
from app.ai.rule_generator import GenerationResult, generate_rule
from app.crawler.fetchers import FetchContext, LoginError
from app.models import AIGeneration, LLMProvider as ProviderRow, Rule
from app.security import decrypt_json
from app.settings_store import get_setting

# 测试中可替换为返回假模型的工厂
provider_factory = build_provider


def provider_config(row: ProviderRow) -> ProviderConfig:
    params = row.params or {}
    return ProviderConfig(
        protocol=row.protocol,
        model=row.model,
        api_key=decrypt_json(row.api_key) or "",
        base_url=row.base_url or None,
        max_tokens=int(params.get("max_tokens") or 8192),
        temperature=params.get("temperature"),
        timeout=float(params.get("timeout") or 120),
        supports_tools=row.supports_tools,
        supports_vision=row.supports_vision,
    )


async def resolve_provider(session: AsyncSession, provider_id: int | None = None) -> tuple[ProviderRow, LLMProvider]:
    stmt = select(ProviderRow).where(ProviderRow.enabled)
    stmt = stmt.where(ProviderRow.id == provider_id) if provider_id else stmt.order_by(ProviderRow.is_default.desc(), ProviderRow.id)
    row = await session.scalar(stmt.limit(1))
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可用的 AI 模型，请先在系统设置中添加并启用模型")
    return row, provider_factory(provider_config(row))


async def used_today(session: AsyncSession) -> int:
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    used = await session.scalar(select(func.count()).select_from(AIGeneration).where(AIGeneration.created_at >= today))
    return used or 0


async def ensure_ai_available(session: AsyncSession) -> None:
    if not await get_setting(session, "ai.enabled"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AI 功能未开启")
    limit = int(await get_setting(session, "ai.daily_limit") or 0)
    if limit and await used_today(session) >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"今日 AI 调用次数已达上限（{limit}）")


async def ai_status(session: AsyncSession) -> tuple[bool, str]:
    """返回 (是否可用, 原因说明)，供界面展示。"""
    try:
        await ensure_ai_available(session)
        row, _ = await resolve_provider(session)
    except HTTPException as exc:
        return False, str(exc.detail)
    return True, f"AI 可用，默认模型：{row.name}（{row.model}）"


async def ai_available(session: AsyncSession) -> bool:
    return (await ai_status(session))[0]


async def run_generation(
    session: AsyncSession,
    ctx: FetchContext,
    fetch_mode: str,
    *,
    hint: str | None = None,
    source_id: int | None = None,
    provider_id: int | None = None,
    trigger: str = "manual",
    previous_rule: dict[str, Any] | None = None,
    analyze_detail: bool = False,
) -> tuple[AIGeneration, GenerationResult]:
    row, provider = await resolve_provider(session, provider_id)
    gen = AIGeneration(
        source_id=source_id, provider_id=row.id, trigger=trigger, url=ctx.start_url, user_hint=hint, status="running"
    )
    session.add(gen)
    await session.commit()

    max_rounds = int(await get_setting(session, "ai.max_rounds") or 3)
    try:
        result = await generate_rule(
            provider, ctx, fetch_mode, hint, max_rounds, previous_rule=previous_rule, analyze_detail=analyze_detail
        )
    except (LLMError, LoginError) as exc:
        gen.status, gen.error = "failed", str(exc)[:4000]
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI 生成失败：{exc}") from exc
    except Exception as exc:
        gen.status, gen.error = "failed", f"{type(exc).__name__}: {exc}"[:4000]
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI 生成失败：{type(exc).__name__}: {exc}") from exc

    gen.status = "success" if result.ok else "failed"
    gen.rounds, gen.item_count = result.rounds, len(result.items)
    gen.result_rule = result.rule.model_dump(by_alias=True) if result.rule else None
    gen.explanation = result.explanation
    gen.prompt_tokens, gen.completion_tokens = result.prompt_tokens, result.completion_tokens
    gen.duration_ms = result.duration_ms
    if not result.ok:
        gen.error = "；".join(result.problems)[:4000] or "未生成可用规则"
    await session.commit()
    return gen, result


async def activate_ai_rule(session: AsyncSession, source_id: int, gen: AIGeneration, note: str) -> Rule:
    last = await session.scalar(select(func.max(Rule.version)).where(Rule.source_id == source_id))
    await session.execute(update(Rule).where(Rule.source_id == source_id).values(is_active=False))
    rule = Rule(
        source_id=source_id, version=(last or 0) + 1, config=gen.result_rule, is_active=True, origin="ai", note=note
    )
    session.add(rule)
    await session.flush()
    gen.result_rule_id = rule.id
    await session.commit()
    return rule
