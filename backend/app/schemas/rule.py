"""采集规则的声明式 JSON 结构（见 docs/ARCHITECTURE.md 3.1）。

规则只包含选择器和白名单内的 transform，不包含任何可执行代码，
因此人工编写或 AI 生成的规则都可以安全执行。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TRANSFORMS = {
    "trim",
    "price",
    "number",
    "int",
    "datetime",
    "absolute_url",
    "lower",
    "status",
}


class FetchConfig(BaseModel):
    mode: Literal["static", "browser", "api"] | None = None  # 为空时使用站点的 fetch_mode
    url: str | None = Field(None, description="api 模式：接口地址（为空则用站点链接）")
    method: Literal["GET", "POST"] = "GET"
    body: dict[str, Any] | None = Field(None, description="api 模式 POST 的 JSON 请求体")
    wait_for: str | None = Field(None, description="浏览器模式下等待出现的 CSS 选择器")
    wait_ms: int = Field(0, ge=0, le=60_000, description="页面加载后额外等待的毫秒数")
    scroll: bool = Field(False, description="是否滚动到底部以触发懒加载")
    timeout: int = Field(30, ge=1, le=180)


class Transform(BaseModel):
    name: str
    pattern: str | None = None  # 仅 regex_replace 使用
    repl: str = ""

    @model_validator(mode="after")
    def _check(self) -> "Transform":
        if self.name == "regex_replace":
            if not self.pattern:
                raise ValueError("regex_replace 需要 pattern")
        elif self.name not in TRANSFORMS:
            raise ValueError(f"不支持的 transform: {self.name}")
        return self


class FieldRule(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    type: Literal["css", "xpath", "regex", "jsonpath"] = "css"
    selector: str = Field("", description="相对拍品容器的选择器；为空表示容器本身")
    attr: str = Field("text", description="text / html / 任意属性名，如 href、src、data-end")
    pattern: str | None = Field(None, description="regex 类型：带一个捕获组的正则")
    from_: str | None = Field(None, alias="from", description="regex 类型：从已抽取的字段取值，为空则用容器文本")
    transform: list[str | Transform] = Field(default_factory=list)
    default: str | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check(self) -> "FieldRule":
        if self.type == "regex" and not self.pattern:
            raise ValueError(f"字段 {self.key}: regex 类型需要 pattern")
        self.transform = [Transform(name=t) if isinstance(t, str) else t for t in self.transform]
        return self


class ListRule(BaseModel):
    item_selector: str = Field(min_length=1)
    item_selector_type: Literal["css", "xpath", "jsonpath"] = "css"
    fields: list[FieldRule] = Field(min_length=1)
    unique_key: str | None = Field(None, description="用于去重的字段名，默认 url")

    @model_validator(mode="after")
    def _check(self) -> "ListRule":
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("字段 key 重复")
        if self.unique_key and self.unique_key not in keys:
            raise ValueError(f"unique_key {self.unique_key} 不在字段中")
        return self


class Pagination(BaseModel):
    type: Literal["none", "next_link", "url_template", "page_param"] = "none"
    selector: str | None = Field(None, description="next_link：下一页链接的 CSS 选择器")
    template: str | None = Field(None, description="url_template：含 {page} 占位符的 URL")
    param: str | None = Field(None, description="page_param：页码参数名（GET 为查询参数，POST 为请求体字段）")
    start: int = 1
    max_pages: int = Field(1, ge=1, le=500)

    @model_validator(mode="after")
    def _check(self) -> "Pagination":
        if self.type == "next_link" and not self.selector:
            raise ValueError("next_link 翻页需要 selector")
        if self.type == "url_template" and (not self.template or "{page}" not in self.template):
            raise ValueError("url_template 翻页需要包含 {page} 的 template")
        if self.type == "page_param" and not self.param:
            raise ValueError("page_param 翻页需要 param")
        return self


class DetailRule(BaseModel):
    """详情页：对列表中的拍品逐个打开详情链接，补充字段（详情页的值优先）。"""

    enabled: bool = False
    url_field: str = Field("url", description="列表中保存详情链接的字段")
    fields: list[FieldRule] = Field(default_factory=list, description="选择器相对整个详情页")
    max_items: int = Field(50, ge=1, le=1000, description="每次采集最多抓取的详情页数量")
    only_new: bool = Field(True, description="只抓取首次出现的拍品的详情页")


class RuleConfig(BaseModel):
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    list: ListRule
    pagination: Pagination = Field(default_factory=Pagination)
    detail: DetailRule = Field(default_factory=DetailRule)

    @model_validator(mode="after")
    def _check(self) -> "RuleConfig":
        is_json = self.list.item_selector_type == "jsonpath"
        if is_json != (self.fetch.mode == "api"):
            raise ValueError("api 模式需要 item_selector_type=jsonpath，反之亦然")
        for f in self.list.fields:
            if is_json and f.type not in ("jsonpath", "regex"):
                raise ValueError(f"api 模式下字段 {f.key} 需使用 jsonpath 或 regex")
            if not is_json and f.type == "jsonpath":
                raise ValueError(f"字段 {f.key}: jsonpath 仅用于 api 模式")
        if self.pagination.type == "next_link" and is_json:
            raise ValueError("api 模式不支持 next_link 翻页，请使用 url_template 或 page_param")
        if self.detail.enabled and any(f.type == "jsonpath" for f in self.detail.fields):
            raise ValueError("详情页字段不支持 jsonpath")
        return self
