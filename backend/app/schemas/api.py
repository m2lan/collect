from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

from app.schemas.rule import RuleConfig


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthIn(BaseModel):
    """站点登录配置。字段为空表示保留原值（编辑时不必重新填写密码/Cookie）。"""

    cookies: str | list[dict[str, Any]] | None = None
    login_url: str | None = None
    username: str | None = None
    password: str | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    logged_in_selector: str | None = None


class AuthOut(BaseModel):
    cookie_count: int = 0
    login_url: str | None = None
    username: str | None = None
    has_password: bool = False
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    logged_in_selector: str | None = None
    has_session: bool = False


FetchMode = Literal["static", "browser"]
AuthType = Literal["none", "cookie", "form"]


class SourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    start_url: HttpUrl
    fetch_mode: FetchMode = "static"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    rate_limit: float = Field(1.0, gt=0, le=20)
    enabled: bool = True


class SourceIn(SourceBase):
    auth: AuthIn = Field(default_factory=AuthIn)
    rule: RuleConfig | None = None


class SourceOut(SourceBase):
    id: int
    auth: AuthOut
    active_rule: dict[str, Any] | None = None
    active_rule_version: int | None = None
    lot_count: int = 0
    last_run: "RunOut | None" = None
    created_at: datetime


class PreviewIn(BaseModel):
    source_id: int | None = Field(None, description="编辑已有站点时传入，以复用已保存的登录信息")
    start_url: HttpUrl
    fetch_mode: FetchMode = "static"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    auth: AuthIn = Field(default_factory=AuthIn)
    rule: RuleConfig
    max_pages: int = Field(1, ge=1, le=5)


class PreviewOut(BaseModel):
    pages: int
    count: int
    items: list[dict[str, Any]]
    errors: list[str]


class RuleIn(BaseModel):
    config: RuleConfig
    note: str | None = None
    activate: bool = True


class RuleOut(BaseModel):
    id: int
    source_id: int
    version: int
    config: dict[str, Any]
    is_active: bool
    origin: str
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: int
    source_id: int
    rule_version: int | None
    trigger: str
    status: str
    pages: int
    items_found: int
    items_new: int
    items_updated: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class LotOut(BaseModel):
    id: int
    source_id: int
    source_name: str | None = None
    unique_key: str
    title: str | None
    url: str | None
    image: str | None
    current_price: Decimal | None
    start_price: Decimal | None
    deal_price: Decimal | None
    currency: str | None
    bid_count: int | None
    status: str | None
    start_time: datetime | None
    end_time: datetime | None
    extra: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class SnapshotOut(BaseModel):
    price: Decimal | None
    bid_count: int | None
    status: str | None
    captured_at: datetime

    model_config = {"from_attributes": True}


class LotDetailOut(LotOut):
    snapshots: list[SnapshotOut]


class Page[T](BaseModel):
    total: int
    items: list[T]
SourceOut.model_rebuild()
