import asyncio

from app.config import get_settings
from app.crawler.runner import run_crawl

_background: set[asyncio.Task] = set()
_pool = None


async def enqueue_crawl(run_id: int) -> None:
    settings = get_settings()
    if not settings.redis_url:
        task = asyncio.create_task(run_crawl(run_id))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await _pool.enqueue_job("crawl_job", run_id)
