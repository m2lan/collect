"""按域名限速：同一主域名的所有请求（跨站点、跨任务、跨 worker 进程）之间至少间隔 N 秒。
配置了 Redis 时用 Redis 协调多进程，否则在进程内协调。"""

import asyncio
import time
from urllib.parse import urlsplit

from app.config import get_settings

_local_next: dict[str, float] = {}
_local_lock = asyncio.Lock()
_redis = None

# 原子地预约下一个可用时间片：返回需要等待的毫秒数
_LUA = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
local start = math.max(now, nxt)
redis.call('SET', KEYS[1], start + interval, 'PX', interval + 60000)
return start - now
"""


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if not host or host.replace(".", "").isdigit() or ":" in host:
        return host  # IP 地址
    parts = host.split(".")
    # 粗略取主域名：a.b.taobao.com -> taobao.com；com.cn 等二级后缀保留三段
    if len(parts) >= 3 and parts[-2] in ("com", "net", "org", "gov", "edu", "co"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


async def throttle(url: str, interval: float) -> float:
    """等待直到可以向该域名发出请求，返回实际等待的秒数。"""
    if interval <= 0:
        return 0.0
    key = domain_of(url)
    settings = get_settings()
    if settings.redis_url:
        global _redis
        if _redis is None:
            from redis.asyncio import Redis

            _redis = Redis.from_url(settings.redis_url)
        wait_ms = int(await _redis.eval(_LUA, 1, f"collect:throttle:{key}", int(time.time() * 1000), int(interval * 1000)))
        wait = wait_ms / 1000
    else:
        async with _local_lock:
            now = time.monotonic()
            start = max(now, _local_next.get(key, 0.0))
            _local_next[key] = start + interval
            wait = start - now
    if wait > 0:
        await asyncio.sleep(wait)
    return max(wait, 0.0)
