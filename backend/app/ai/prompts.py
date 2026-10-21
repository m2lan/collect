import json
from typing import Any

from app.schemas.rule import RuleConfig

TOOL_NAME = "submit_rule"
TOOL_DESCRIPTION = "提交为该拍卖列表页生成的采集规则及说明"

SYSTEM_PROMPT = """你是网页数据采集专家，负责为拍卖平台的拍品列表页编写声明式采集规则（JSON）。
你会收到页面 URL、精简后的 HTML（重复结构只保留了前几个样本，并用注释标出省略数量），有时还有截图和页面发出的 JSON 接口响应。

## 规则结构
- fetch：抓取参数。mode 一般留空（沿用站点设置）；浏览器模式下可设置 wait_for（拍品出现后才抓取的 CSS 选择器）、scroll（是否滚动加载）、wait_ms。
- list.item_selector：匹配"每一个拍品容器"的选择器（每个拍品恰好匹配一个元素）。item_selector_type 为 css 或 xpath。
- list.fields：每个字段 {key, type, selector, attr, pattern, from, transform}
  - type=css/xpath 时 selector 相对拍品容器；selector 为空表示容器本身
  - attr：text（全部文本）、html，或属性名如 href、src、data-src、data-end
  - type=regex：从 from 指定的已抽取字段（或容器文本）中用带一个捕获组的 pattern 提取
  - transform（按顺序执行）：trim、price（解析金额与币种，支持“万/亿”）、number、int、datetime、absolute_url、status（把“进行中/已成交/流拍/即将开始”等映射为标准状态）、lower，
    或 {"name":"regex_replace","pattern":"...","repl":"..."}
- list.unique_key：用于去重的字段名，优先 url 或拍品编号。
- pagination：type=none / next_link（selector 为“下一页”链接的 CSS 选择器）/ url_template（template 含 {page}，start 起始页码，max_pages）。

## 标准字段名（尽量使用）
title 标题、url 详情链接、image 图片、current_price 当前价、start_price 起拍价、deal_price 成交价、bid_count 出价次数、
status 状态、start_time 开始时间、end_time 结束时间。其他有价值的信息（如拍品编号 lot_no、评估价 estimate、保证金 deposit、围观数 views、拍卖行 auction_house）可自定义 key。

## 编写要求
1. 选择器要稳定：优先使用语义化的 class / id / 标签结构，避免 nth-child 序号和看起来是随机生成的 class（如 css-1a2b3c、sc-xyz）。
2. 字段选择器必须相对于拍品容器，且在每个拍品中都能匹配。
3. 链接和图片加 absolute_url；价格加 price；数字加 int；时间加 datetime；状态加 status。懒加载图片优先取 data-src / data-original。
4. 文本类字段加 trim。只抽取页面上真实存在的信息，不要臆造选择器。
5. 如果页面存在“下一页”链接或明显的页码 URL 规律，配置 pagination，max_pages 默认 5。
6. explanation 用中文简要说明 item_selector 的依据和每个字段的选择理由。
"""


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """展开 $ref，兼容对 $defs 支持不好的 OpenAI 兼容服务。"""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "explanation": {"type": "string", "description": "中文说明：容器与各字段的选择依据"},
            "rule": _inline_refs(RuleConfig.model_json_schema(by_alias=True)),
        },
        "required": ["explanation", "rule"],
    }


def first_message(url: str, compressed_html: str, hint: str | None, fetch_mode: str, json_samples: list[dict] | None) -> str:
    parts = [f"页面 URL：{url}", f"站点抓取方式：{fetch_mode}"]
    if hint:
        parts.append(f"管理员的额外要求：{hint}")
    if json_samples:
        parts.append(
            "页面加载时捕获到的 JSON 接口（可能包含拍品数据，仅供理解页面，规则仍需基于 HTML）：\n"
            + json.dumps(json_samples, ensure_ascii=False)[:8000]
        )
    parts.append("精简后的页面 HTML：\n" + compressed_html)
    parts.append(f"请调用 {TOOL_NAME} 提交采集规则。")
    return "\n\n".join(parts)


def repair_message(problems: list[str], items: list[dict], current_rule: dict | None = None) -> str:
    parts = ["按你的规则在该页面试跑后发现以下问题，请修正后重新提交完整规则："]
    parts += [f"- {p}" for p in problems]
    if items:
        parts.append("前 3 条抽取结果：\n" + json.dumps(items[:3], ensure_ascii=False, default=str)[:4000])
    if current_rule:
        parts.append("当前规则：\n" + json.dumps(current_rule, ensure_ascii=False))
    return "\n".join(parts)
