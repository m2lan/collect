from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.models import CrawlRun
from app.models.entities import utcnow
from app.schemas.api import Page, RunDetailOut, RunOut

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=Page[RunOut])
async def list_runs(
    session: SessionDep,
    source_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> Page[RunOut]:
    stmt = select(CrawlRun)
    count = select(func.count()).select_from(CrawlRun)
    if source_id:
        stmt, count = stmt.where(CrawlRun.source_id == source_id), count.where(CrawlRun.source_id == source_id)
    runs = (await session.scalars(stmt.order_by(CrawlRun.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return Page[RunOut](total=await session.scalar(count) or 0, items=[RunOut.model_validate(r) for r in runs])


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: int, session: SessionDep) -> CrawlRun:
    run = await session.get(CrawlRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "采集记录不存在")
    return run


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: int, session: SessionDep) -> CrawlRun:
    """停止采集：排队中的直接取消；执行中的在当前页面完成后停止，已抓到的数据照常保存。"""
    run = await session.get(CrawlRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "采集记录不存在")
    if run.status == "pending":
        run.status, run.error, run.finished_at = "cancelled", "已手动取消", utcnow()
    elif run.status == "running":
        run.status = "cancelling"
    elif run.status != "cancelling":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该采集已结束")
    await session.commit()
    return run
