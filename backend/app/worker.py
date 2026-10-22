"""arq worker：`arq app.worker.WorkerSettings`"""

from arq.connections import RedisSettings

from app.config import get_settings
from app.crawler.runner import run_crawl


async def crawl_job(ctx: dict, run_id: int) -> None:
    await run_crawl(run_id)


async def shutdown(ctx: dict) -> None:
    from app.crawler.fetchers.pool import pool

    await pool.close()


class WorkerSettings:
    functions = [crawl_job]
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379")
    max_jobs = 4
    job_timeout = 3600
