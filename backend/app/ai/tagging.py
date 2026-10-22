"""AI 结构化标注：从拍品标题与扩展信息中抽取品类、年代、材质等标签，用于筛选、订阅和行情统计。"""

import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.providers import LLMError, Message
from app.models import AIGeneration, Lot
from app.models.entities import utcnow

log = logging.getLogger(__name__)

BATCH = 20
CATEGORIES = [
    "瓷器陶器", "书画", "玉器翡翠", "珠宝首饰", "钟表", "奢侈品箱包", "酒水", "钱币邮票", "文玩杂项", "古董家具",
    "住宅房产", "商业房产", "土地", "机动车", "机械设备", "股权债权", "电子数码", "家居电器", "原材料", "其他",
]
TAG_FIELDS = {
    "category": "品类（必须从给定列表中选择）",
    "sub_category": "细分品类，如 青花瓷、手镯、SUV、挖掘机",
    "era": "年代/朝代，如 清乾隆、民国、当代、2015年",
    "material": "材质，如 瓷、和田玉、18K金、钢",
    "origin": "产地/地区，如 景德镇、缅甸、上海市",
    "brand": "品牌或作者",
    "condition": "品相/成色，如 全新、九成新、有瑕疵",
    "keywords": "3-6 个检索关键词（数组）",
}

SYSTEM = f"""你是拍卖拍品的资深分类员。请根据每个拍品的标题和附加信息，抽取结构化标签。
品类必须从以下列表中选择：{"、".join(CATEGORIES)}。
无法判断的字段留空字符串，不要臆造。keywords 为数组，其余字段为字符串。
字段说明：{json.dumps(TAG_FIELDS, ensure_ascii=False)}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    **{k: ({"type": "array", "items": {"type": "string"}} if k == "keywords" else {"type": "string"})
                       for k in TAG_FIELDS},
                },
                "required": ["id", "category"],
            },
        }
    },
    "required": ["items"],
}


def _lot_brief(lot: Lot) -> dict[str, Any]:
    extra = {k: v for k, v in (lot.extra or {}).items() if isinstance(v, (str, int, float)) and len(str(v)) < 200}
    return {"id": lot.id, "title": lot.title, "extra": extra}


def clean_tags(raw: dict[str, Any]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for k in TAG_FIELDS:
        v = raw.get(k)
        if k == "keywords":
            v = [str(x).strip() for x in (v or []) if str(x).strip()][:8]
        else:
            v = str(v or "").strip()
        if v:
            tags[k] = v
    if tags.get("category") not in CATEGORIES:
        tags["category"] = "其他"
    return tags


async def tag_lots(session: AsyncSession, lot_ids: list[int] | None = None, limit: int = 200) -> dict[str, int]:
    """为指定（或尚未标注的）拍品打标签，返回统计。调用方负责检查 AI 是否可用。"""
    stmt = select(Lot)
    stmt = stmt.where(Lot.id.in_(lot_ids)) if lot_ids else stmt.where(Lot.tagged_at.is_(None)).order_by(Lot.id.desc())
    lots = list((await session.scalars(stmt.limit(limit))).all())
    if not lots:
        return {"tagged": 0, "batches": 0, "failed": 0}
    row, provider = await service.resolve_provider(session)
    stats = {"tagged": 0, "batches": 0, "failed": 0}
    for i in range(0, len(lots), BATCH):
        batch = lots[i : i + BATCH]
        started = time.monotonic()
        gen = AIGeneration(provider_id=row.id, trigger="tag", url=f"标注 {len(batch)} 件拍品", status="running")
        session.add(gen)
        await session.commit()
        try:
            res = await provider.generate_structured(
                SYSTEM, [Message("user", json.dumps([_lot_brief(l) for l in batch], ensure_ascii=False))],
                SCHEMA, "submit_tags", "提交拍品标签",
            )
        except LLMError as exc:
            gen.status, gen.error = "failed", str(exc)[:2000]
            await session.commit()
            stats["failed"] += len(batch)
            continue
        by_id = {lot.id: lot for lot in batch}
        done = 0
        for item in res.data.get("items") or []:
            lot = by_id.get(int(item.get("id", 0) or 0))
            if lot is None:
                continue
            lot.tags, lot.tagged_at = clean_tags(item), utcnow()
            done += 1
        gen.status = "success" if done else "failed"
        gen.item_count, gen.rounds = done, 1
        gen.prompt_tokens, gen.completion_tokens = res.prompt_tokens, res.completion_tokens
        gen.duration_ms = int((time.monotonic() - started) * 1000)
        await session.commit()
        stats["tagged"] += done
        stats["failed"] += len(batch) - done
        stats["batches"] += 1
    return stats


async def auto_tag_new(lot_ids: list[int]) -> None:
    """采集后自动标注新拍品（需开启 ai.auto_tag）。"""
    from fastapi import HTTPException

    from app.db import SessionLocal
    from app.settings_store import get_setting

    if not lot_ids:
        return
    async with SessionLocal() as session:
        if not await get_setting(session, "ai.auto_tag"):
            return
        try:
            await service.ensure_ai_available(session)
            await tag_lots(session, lot_ids, limit=len(lot_ids))
        except HTTPException as exc:
            log.info("auto tag skipped: %s", exc.detail)
