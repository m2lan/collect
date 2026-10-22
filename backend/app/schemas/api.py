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
    session_saved_at: str | None = None


FetchMode = Literal["static", "browser"]
AuthType = Literal["none", "cookie", "form", "session"]


class SourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    start_url: HttpUrl
    fetch_mode: FetchMode = "static"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    rate_limit: float = Field(1.0, gt=0, le=20)
    enabled: bool = True
    allow_ai_repair: bool = True
    use_proxy: bool = False
    ending_boost: bool = False


class SourceIn(SourceBase):
    auth: AuthIn = Field(default_factory=AuthIn)
    rule: RuleConfig | None = None
    rule_origin: Literal["manual", "visual", "ai"] = "manual"


class SourceOut(SourceBase):
    id: int
    auth: AuthOut
    active_rule: dict[str, Any] | None = None
    active_rule_version: int | None = None
    lot_count: int = 0
    health: str = "ok"
    health_message: str | None = None
    login_expired_at: datetime | None = None
    last_run: "RunOut | None" = None
    created_at: datetime


class PreviewIn(BaseModel):
    source_id: int | None = Field(None, description="编辑已有站点时传入，以复用已保存的登录信息")
    start_url: HttpUrl
    fetch_mode: FetchMode = "static"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    auth: AuthIn = Field(default_factory=AuthIn)
    use_proxy: bool = False
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
    kind: str = "full"
    status: str
    pages: int
    items_found: int
    items_new: int
    items_updated: int
    error: str | None
    error_type: str | None = None
    duration_ms: int | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class RunDetailOut(RunOut):
    log: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleIn(BaseModel):
    kind: Literal["full", "live"] = "full"
    cron: str = Field(min_length=9, max_length=64, description="5 段 cron 表达式，如 0 */2 * * *")
    enabled: bool = True


class ScheduleOut(ScheduleIn):
    id: int
    source_id: int
    next_run_at: datetime | None
    last_run_at: datetime | None

    model_config = {"from_attributes": True}


class SnapshotIn(BaseModel):
    source_id: int | None = None
    start_url: HttpUrl
    fetch_mode: FetchMode = "static"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    auth: AuthIn = Field(default_factory=AuthIn)
    use_proxy: bool = False


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


class AISettings(BaseModel):
    enabled: bool = False
    daily_limit: int = Field(100, ge=0)
    auto_repair: bool = False
    max_rounds: int = Field(3, ge=1, le=6)


class AlertSettings(BaseModel):
    failure_threshold: int = Field(2, ge=1, le=20)
    webhook_type: Literal["none", "generic", "wecom", "dingtalk", "feishu"] = "none"
    webhook_url: str = ""


class CrawlerSettings(BaseModel):
    proxies: list[str] = Field(default_factory=list, description="代理地址列表，如 http://user:pass@host:port")
    random_ua: bool = True
    stealth: bool = True
    domain_interval: float = Field(1.0, ge=0, le=60)
    boost_window_minutes: int = Field(30, ge=1, le=1440)
    boost_interval_minutes: float = Field(1, ge=0.5, le=60)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    fetch_mode: FetchMode = "static"
    config: RuleConfig


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    fetch_mode: str
    config: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: Literal["openai", "anthropic"]
    base_url: str | None = None
    api_key: str | None = Field(None, description="为空表示保留原值")
    model: str = Field(min_length=1)
    max_tokens: int = Field(8192, ge=256, le=200_000)
    temperature: float | None = Field(None, ge=0, le=2)
    timeout: int = Field(120, ge=10, le=600)
    supports_tools: bool = True
    supports_vision: bool = False
    is_default: bool = False
    enabled: bool = True


class ProviderOut(BaseModel):
    id: int
    name: str
    protocol: str
    base_url: str | None
    api_key_masked: str
    model: str
    max_tokens: int
    temperature: float | None
    timeout: int
    supports_tools: bool
    supports_vision: bool
    is_default: bool
    enabled: bool
    created_at: datetime


class GenerateIn(BaseModel):
    source_id: int | None = Field(None, description="已有站点：复用登录信息；apply=true 时直接保存为新规则版本并启用")
    start_url: HttpUrl
    fetch_mode: FetchMode = "browser"
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: AuthType = "none"
    auth: AuthIn = Field(default_factory=AuthIn)
    use_proxy: bool = False
    hint: str | None = Field(None, max_length=2000)
    provider_id: int | None = None
    apply: bool = False
    analyze_detail: bool = Field(False, description="同时打开详情页生成详情字段规则（AI 判断列表页缺少所需字段时也会自动进行）")


class GenerateOut(BaseModel):
    generation_id: int
    ok: bool
    rule: dict[str, Any] | None
    explanation: str
    problems: list[str]
    items: list[dict[str, Any]]
    count: int
    rounds: int
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    rule_version: int | None = None
    missing_fields: list[str] = Field(default_factory=list)


class GenerationOut(BaseModel):
    id: int
    source_id: int | None
    provider_id: int | None
    trigger: str
    url: str
    user_hint: str | None
    status: str
    rounds: int
    item_count: int
    result_rule_id: int | None
    explanation: str | None
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


SubscriptionEvent = Literal["new", "price_change", "price_reach", "ending_soon"]


class SubscriptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    events: list[SubscriptionEvent] = Field(min_length=1)
    target_price: Decimal | None = None
    ending_minutes: int = Field(30, ge=1, le=1440)
    webhook_type: Literal["generic", "wecom", "dingtalk", "feishu"] | None = None
    webhook_url: str | None = None


class SubscriptionOut(SubscriptionIn):
    id: int
    created_at: datetime
    notified: int = 0

    model_config = {"from_attributes": True}


class NotificationOut(BaseModel):
    id: int
    subscription_id: int
    subscription_name: str | None = None
    lot_id: int
    event: str
    message: str
    sent: bool
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
