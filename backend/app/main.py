import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, update

from app.api import ai, analytics, auth, backups, dashboard, lots, remote, runs, sources, subscriptions, system, templates, users
from app.api.deps import require
from app.audit import AuditMiddleware
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
            session.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password),
                             role="admin", is_superuser=True))
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
    from app.crawler.fetchers.pool import pool
    from app.remote import manager

    await manager.close_all()
    await pool.close()


app = FastAPI(title="拍卖价格采集平台", lifespan=lifespan)
app.add_middleware(AuditMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"]
)

api = APIRouter(prefix="/api")
api.include_router(auth.router)
def guard(read: str = "viewer", write: str = "operator") -> list:
    return [Depends(require(read, write))]


# 权限：只读可查看数据；操作员管理站点、规则、采集、订阅；管理员负责系统设置、模型、用户与备份
api.include_router(sources.router, dependencies=guard())
api.include_router(sources.preview_router, dependencies=guard("operator", "operator"))
api.include_router(lots.router, dependencies=guard())
api.include_router(runs.router, dependencies=guard())
api.include_router(ai.router, dependencies=guard())
api.include_router(system.router, dependencies=guard("admin", "admin"))
api.include_router(templates.router, dependencies=guard())
api.include_router(remote.router, dependencies=guard("operator", "operator"))
api.include_router(dashboard.router, dependencies=guard())
api.include_router(backups.router, dependencies=guard("admin", "admin"))
api.include_router(subscriptions.router, dependencies=guard())
api.include_router(analytics.router, dependencies=guard())
api.include_router(users.router, dependencies=guard("admin", "admin"))
api.include_router(users.audit_router, dependencies=guard("admin", "admin"))
app.include_router(api)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
