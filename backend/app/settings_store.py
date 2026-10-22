"""系统设置（key/value 存数据库），带默认值。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

DEFAULTS: dict[str, Any] = {
    "ai.enabled": False,
    "ai.daily_limit": 100,  # 每日最多调用 AI 生成次数，0 表示不限
    "ai.auto_repair": False,  # 规则失效时自动调用 AI 修复并直接启用
    "ai.max_rounds": 3,  # 生成 + 自我修正的最大轮数
    "alert.failure_threshold": 2,  # 连续失败多少次判定规则失效
    "alert.webhook_type": "none",  # none / generic / wecom / dingtalk / feishu
    "alert.webhook_url": "",
    "crawler.proxies": [],  # 代理池，站点开启"使用代理"后每次采集随机选一个
    "crawler.random_ua": True,  # 每次采集随机选择 User-Agent（站点自定义请求头优先）
    "crawler.stealth": True,  # 浏览器指纹伪装
    "crawler.domain_interval": 1.0,  # 同一主域名请求的最小间隔（秒，跨站点 / 任务共享），0 不限
}


async def get_setting(session: AsyncSession, key: str) -> Any:
    row = await session.get(SystemSetting, key)
    return row.value if row else DEFAULTS.get(key)


async def get_settings_dict(session: AsyncSession, prefix: str = "") -> dict[str, Any]:
    return {k: await get_setting(session, k) for k in DEFAULTS if k.startswith(prefix)}


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        session.add(SystemSetting(key=key, value=value))
