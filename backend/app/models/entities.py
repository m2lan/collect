from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Index,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)  # 兼容旧字段，等同 role == admin
    # admin 管理员（全部权限）/ operator 操作员（站点、规则、采集、订阅）/ viewer 只读
    role: Mapped[str] = mapped_column(String(16), default="viewer", server_default="viewer")
    display_name: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    start_url: Mapped[str] = mapped_column(Text)
    fetch_mode: Mapped[str] = mapped_column(String(16), default="static")
    headers: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    auth_type: Mapped[str] = mapped_column(String(16), default="none")
    # 加密后的登录配置（cookie 列表 / 表单登录配置）与登录后的浏览器状态
    auth_config: Mapped[str | None] = mapped_column(Text)
    storage_state: Mapped[str | None] = mapped_column(Text)
    rate_limit: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 规则健康状况：ok / warning（字段缺失较多）/ broken（连续采集失败）
    health: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    health_message: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    allow_ai_repair: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    use_proxy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 临近结束加密盯价：有拍品即将结束时自动提高刷新频率
    ending_boost: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 登录失效时间（Cookie / 人工登录会话失效后记录，成功采集后清空）
    login_expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    rules: Mapped[list["Rule"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Rule(Base):
    __tablename__ = "rule"
    __table_args__ = (UniqueConstraint("source_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(16), default="manual")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="rules")


class CrawlRun(Base):
    __tablename__ = "crawl_run"
    __table_args__ = (Index("ix_crawl_run_created_status", "created_at", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    rule_version: Mapped[int | None] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    kind: Mapped[str] = mapped_column(String(16), default="full")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(16), index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Lot(Base):
    __tablename__ = "lot"
    __table_args__ = (UniqueConstraint("source_id", "unique_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    unique_key: Mapped[str] = mapped_column(String(512))
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str | None] = mapped_column(Text)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    start_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    deal_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    bid_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(16), index=True)
    start_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # AI 结构化标签：{"category": "瓷器", "era": "清", "material": "...", ...}
    tags: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, server_default="{}")
    tagged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id", ondelete="CASCADE"), index=True)
    crawl_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_run.id", ondelete="SET NULL"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bid_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(16))
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LLMProvider(Base):
    __tablename__ = "llm_provider"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(16))  # openai / anthropic
    base_url: Mapped[str | None] = mapped_column(Text)
    api_key: Mapped[str | None] = mapped_column(Text)  # 加密
    model: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # max_tokens / temperature / timeout
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AIGeneration(Base):
    __tablename__ = "ai_generation"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id", ondelete="SET NULL"), index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("llm_provider.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual / repair
    url: Mapped[str] = mapped_column(Text)
    user_hint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / success / failed
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    result_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    result_rule_id: Mapped[int | None] = mapped_column(ForeignKey("rule.id", ondelete="SET NULL"))
    explanation: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType)


class Schedule(Base):
    """定时任务。kind=full：完整采集；kind=live：只刷新进行中的拍品（盯价）。"""

    __tablename__ = "schedule"
    __table_args__ = (UniqueConstraint("source_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="full")
    cron: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RuleTemplate(Base):
    """规则模板：常见平台的规则沉淀下来，新建站点时一键套用。"""

    __tablename__ = "rule_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    fetch_mode: Mapped[str] = mapped_column(String(16), default="static")
    config: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Subscription(Base):
    """订阅：符合条件的拍品发生指定事件时推送通知。"""

    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[list[str]] = mapped_column(JSONType, default=list)  # 标题包含任一关键词（空 = 不限）
    exclude_keywords: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source_ids: Mapped[list[int]] = mapped_column(JSONType, default=list)  # 空 = 全部站点
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)  # AI 标签包含任一（空 = 不限）
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # 事件：new 新上架 / price_change 价格变化 / price_reach 价格达到 target_price / ending_soon 即将结束
    events: Mapped[list[str]] = mapped_column(JSONType, default=list)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    ending_minutes: Mapped[int] = mapped_column(Integer, default=30)
    webhook_type: Mapped[str | None] = mapped_column(String(16))  # 为空使用全局告警通知
    webhook_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Notification(Base):
    """已发送的订阅通知（同一订阅、拍品、事件只通知一次，价格变化按价格区分）。"""

    __tablename__ = "notification"
    __table_args__ = (UniqueConstraint("subscription_id", "lot_id", "event", "dedupe_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscription.id", ondelete="CASCADE"), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(16))
    dedupe_key: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AuditLog(Base):
    """操作日志：谁在什么时候做了什么。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    status_code: Mapped[int] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
