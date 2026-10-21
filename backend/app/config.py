from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./collect.db"
    # 为空时采集任务在 API 进程内以后台协程执行（便于本地开发）；生产环境配置 Redis 走 arq worker
    redis_url: str = ""

    secret_key: str = "change-me-in-production"
    jwt_expire_hours: int = 24

    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 浏览器模式：留空使用 Playwright 默认浏览器
    browser_executable_path: str = ""
    browser_headless: bool = True

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
