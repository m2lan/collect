from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select

from app.api.deps import SessionDep
from app.models import Lot, PriceSnapshot, Source
from app.schemas.api import LotDetailOut, LotOut, Page

router = APIRouter(prefix="/lots", tags=["lots"])

SortField = Literal[
    "last_seen_at", "-last_seen_at", "current_price", "-current_price",
    "end_time", "-end_time", "bid_count", "-bid_count", "first_seen_at", "-first_seen_at",
]


def _filtered(
    stmt: Select, source_id: int | None, status_: str | None, keyword: str | None,
    min_price: float | None, max_price: float | None,
) -> Select:
    if source_id:
        stmt = stmt.where(Lot.source_id == source_id)
    if status_:
        stmt = stmt.where(Lot.status == status_)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Lot.title.ilike(like), Lot.unique_key.ilike(like)))
    price = func.coalesce(Lot.deal_price, Lot.current_price)
    if min_price is not None:
        stmt = stmt.where(price >= min_price)
    if max_price is not None:
        stmt = stmt.where(price <= max_price)
    return stmt


@router.get("", response_model=Page[LotOut])
async def list_lots(
    session: SessionDep,
    source_id: int | None = None,
    status_: str | None = Query(None, alias="status"),
    keyword: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: SortField = "-last_seen_at",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> Page[LotOut]:
    args = (source_id, status_, keyword, min_price, max_price)
    total = await session.scalar(_filtered(select(func.count()).select_from(Lot), *args))
    column = getattr(Lot, sort.lstrip("-"))
    order = column.desc().nulls_last() if sort.startswith("-") else column.asc().nulls_last()
    stmt = (
        _filtered(select(Lot, Source.name).join(Source, Source.id == Lot.source_id), *args)
        .order_by(order, Lot.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()
    items = [LotOut.model_validate(lot).model_copy(update={"source_name": name}) for lot, name in rows]
    return Page[LotOut](total=total or 0, items=items)


@router.get("/{lot_id}", response_model=LotDetailOut)
async def get_lot(lot_id: int, session: SessionDep) -> LotDetailOut:
    lot = await session.get(Lot, lot_id)
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "拍品不存在")
    source = await session.get(Source, lot.source_id)
    snapshots = (
        await session.scalars(select(PriceSnapshot).where(PriceSnapshot.lot_id == lot_id).order_by(PriceSnapshot.captured_at))
    ).all()
    base = LotOut.model_validate(lot).model_dump()
    return LotDetailOut(**{**base, "source_name": source.name if source else None}, snapshots=snapshots)
