from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="rules")


class CrawlRun(Base):
    __tablename__ = "crawl_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    rule_version: Mapped[int | None] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id", ondelete="CASCADE"), index=True)
    crawl_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_run.id", ondelete="SET NULL"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bid_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(16))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
