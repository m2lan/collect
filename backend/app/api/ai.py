from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.ai import service
from app.ai.providers import LLMError, Message
from app.api.deps import SessionDep
from app.api.sources import build_context
from app.models import AIGeneration, LLMProvider, Source
from app.schemas.api import (
    AISettings,
    GenerateIn,
    GenerateOut,
    GenerationOut,
    Page,
    ProviderIn,
    ProviderOut,
)
from app.security import decrypt_json, encrypt_json
from app.settings_store import get_settings_dict, set_setting

router = APIRouter(prefix="/ai", tags=["ai"])


def _mask(key: str | None) -> str:
    if not key:
        return ""
    return key[:3] + "****" + key[-4:] if len(key) > 10 else "****"


def _provider_out(row: LLMProvider) -> ProviderOut:
    p = row.params or {}
    return ProviderOut(
        id=row.id, name=row.name, protocol=row.protocol, base_url=row.base_url,
        api_key_masked=_mask(decrypt_json(row.api_key)), model=row.model,
        max_tokens=int(p.get("max_tokens") or 8192), temperature=p.get("temperature"),
        timeout=int(p.get("timeout") or 120), supports_tools=row.supports_tools,
        supports_vision=row.supports_vision, is_default=row.is_default, enabled=row.enabled, created_at=row.created_at,
    )


@router.get("/settings")
async def get_ai_settings(session: SessionDep) -> dict:
    s = await get_settings_dict(session, "ai.")
    settings = AISettings(**{k.removeprefix("ai."): v for k, v in s.items()})
    return {**settings.model_dump(), "available": await service.ai_available(session)}


@router.put("/settings")
async def put_ai_settings(body: AISettings, session: SessionDep) -> dict:
    for k, v in body.model_dump().items():
        await set_setting(session, f"ai.{k}", v)
    await session.commit()
    return await get_ai_settings(session)


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(session: SessionDep) -> list[ProviderOut]:
    rows = (await session.scalars(select(LLMProvider).order_by(LLMProvider.id))).all()
    return [_provider_out(r) for r in rows]


def _apply(row: LLMProvider, body: ProviderIn) -> None:
    for f in ("name", "protocol", "model", "supports_tools", "supports_vision", "is_default", "enabled"):
        setattr(row, f, getattr(body, f))
    row.base_url = (body.base_url or "").strip() or None
    row.params = {"max_tokens": body.max_tokens, "temperature": body.temperature, "timeout": body.timeout}
    if body.api_key:
        row.api_key = encrypt_json(body.api_key.strip())


async def _single_default(session: SessionDep, row: LLMProvider) -> None:
    if row.is_default:
        await session.execute(update(LLMProvider).where(LLMProvider.id != row.id).values(is_default=False))


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderIn, session: SessionDep) -> ProviderOut:
    if not body.api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写 API Key")
    row = LLMProvider()
    _apply(row, body)
    if not await session.scalar(select(func.count()).select_from(LLMProvider)):
        row.is_default = True
    session.add(row)
    await session.flush()
    await _single_default(session, row)
    await session.commit()
    return _provider_out(row)


async def _get_provider(session: SessionDep, provider_id: int) -> LLMProvider:
    row = await session.get(LLMProvider, provider_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型配置不存在")
    return row


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: int, body: ProviderIn, session: SessionDep) -> ProviderOut:
    row = await _get_provider(session, provider_id)
    _apply(row, body)
    await _single_default(session, row)
    await session.commit()
    return _provider_out(row)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: int, session: SessionDep) -> None:
    await session.delete(await _get_provider(session, provider_id))
    await session.commit()


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int, session: SessionDep) -> dict:
    """发送一个最小的结构化输出请求，验证 Key、模型与工具调用是否可用。"""
    row = await _get_provider(session, provider_id)
    provider = service.provider_factory(service.provider_config(row))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "model": {"type": "string"}}, "required": ["ok"]}
    try:
        result = await provider.generate_structured(
            "你是连通性测试助手。", [Message("user", "请调用工具，ok 设为 true，model 填你的模型名称。")],
            schema, "report", "上报测试结果",
        )
    except LLMError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": bool(result.data.get("ok")), "message": f"连接成功：{result.data}", "usage": [result.prompt_tokens, result.completion_tokens]}


@router.post("/generate", response_model=GenerateOut)
async def generate(body: GenerateIn, session: SessionDep) -> GenerateOut:
    await service.ensure_ai_available(session)
    source = await session.get(Source, body.source_id) if body.source_id else None
    if body.source_id and source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
    ctx = await build_context(session, body, source)
    gen, result = await service.run_generation(
        session, ctx, body.fetch_mode, hint=body.hint, source_id=body.source_id, provider_id=body.provider_id
    )
    version = None
    if body.apply and source and result.ok:
        rule = await service.activate_ai_rule(session, source.id, gen, note=body.hint or "AI 生成")
        version = rule.version
    return GenerateOut(
        generation_id=gen.id, ok=result.ok, rule=gen.result_rule, explanation=result.explanation,
        problems=result.problems, items=result.items[:100], count=len(result.items), rounds=result.rounds,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        duration_ms=result.duration_ms, rule_version=version,
    )


@router.get("/generations", response_model=Page[GenerationOut])
async def list_generations(
    session: SessionDep, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
) -> Page[GenerationOut]:
    total = await session.scalar(select(func.count()).select_from(AIGeneration))
    rows = (
        await session.scalars(
            select(AIGeneration).order_by(AIGeneration.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return Page[GenerationOut](total=total or 0, items=[GenerationOut.model_validate(r) for r in rows])


@router.get("/usage")
async def usage(session: SessionDep) -> dict:
    row = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(AIGeneration.prompt_tokens), 0), func.coalesce(func.sum(AIGeneration.completion_tokens), 0))
        )
    ).one()
    return {"calls": row[0], "prompt_tokens": row[1], "completion_tokens": row[2]}
