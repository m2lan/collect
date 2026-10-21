import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, update

from app.api import ai, auth, lots, runs, sources
from app.api.deps import superuser
from app.config import get_settings
from app.db import SessionLocal
from app.models import CrawlRun, User
from app.models.entities import utcnow
from app.scheduler import run_scheduler
from app.security import hash_password

logging.basicConfig(level=logging.INFO)


async def init_admin() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        if not await session.scalar(select(func.count()).select_from(User)):
            session.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
        if not settings.redis_url:
            # 进程内执行模式下，重启会中断任务，标记为失败以免一直占用"执行中"状态
            await session.execute(
                update(CrawlRun)
                .where(CrawlRun.status.in_(["pending", "running"]))
                .values(status="failed", error="服务重启，任务中断", finished_at=utcnow())
            )
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_admin()
    scheduler = None
    if not get_settings().redis_url and get_settings().embedded_scheduler:
        # 进程内模式：调度器随 API 启动；Redis 模式下由独立的 scheduler 服务负责
        scheduler = asyncio.create_task(run_scheduler())
    yield
    if scheduler:
        scheduler.cancel()


app = FastAPI(title="拍卖价格采集平台", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"]
)

api = APIRouter(prefix="/api")
api.include_router(auth.router)
protected = [Depends(superuser)]
api.include_router(sources.router, dependencies=protected)
api.include_router(sources.preview_router, dependencies=protected)
api.include_router(lots.router, dependencies=protected)
api.include_router(runs.router, dependencies=protected)
api.include_router(ai.router, dependencies=protected)
app.include_router(api)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
