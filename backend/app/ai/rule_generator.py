"""AI 生成采集规则：抓取页面 → 压缩 → LLM 生成 → 校验 → 试跑 → 根据问题自我修正。

AI 只负责产出声明式规则，日常采集仍由确定性的抽取器执行。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.ai.html_compress import compress_html
from app.ai.prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    first_message,
    repair_message,
    tool_schema,
)
from app.ai.providers import LLMError, LLMProvider, Message
from app.crawler.extractor import PageResult, extract_json, extract_page
from app.crawler.fetchers import FetchContext, open_fetcher
from app.crawler.pipeline import jsonable
from app.crawler.sniff import find_candidates
from app.schemas.rule import FetchConfig, RuleConfig

log = logging.getLogger(__name__)

PRICE_KEYS = ("current_price", "deal_price", "start_price", "price")


@dataclass
class PageSnapshot:
    url: str
    html: str
    screenshot: bytes | None = None
    json_responses: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GenerationResult:
    rule: RuleConfig | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    problems: list[str] = field(default_factory=list)
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    ok: bool = False


async def capture_page(ctx: FetchContext, fetch_mode: str, want_screenshot: bool) -> PageSnapshot:
    browser = fetch_mode == "browser"
    fetch = FetchConfig(mode=fetch_mode, wait_ms=1500 if browser else 0, scroll=browser, timeout=45)  # type: ignore[arg-type]
    async with open_fetcher(ctx, fetch, capture_json=browser) as fetcher:
        fetched = await fetcher.fetch(ctx.start_url)
        shot = None
        if browser and want_screenshot and hasattr(fetcher, "screenshot"):
            shot = await fetcher.screenshot()  # type: ignore[attr-defined]
    return PageSnapshot(fetched.url, fetched.html, shot, fetched.json_responses)


def summarize_json(responses: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """挑出最像拍品列表的 JSON 接口，只保留结构样本。"""
    return [
        {k: c[k] for k in ("url", "method", "body", "items_path", "count", "sample")}
        for c in find_candidates(responses, limit)
    ]


def _api_page(config: RuleConfig, snapshot: PageSnapshot) -> PageResult | None:
    target = urlsplit(config.fetch.url or snapshot.url)._replace(query="", fragment="").geturl()
    for r in snapshot.json_responses:
        if urlsplit(r["url"])._replace(query="", fragment="").geturl() == target:
            return extract_json(r["data"], r["url"], config)
    return None


def evaluate(config: RuleConfig, snapshot: PageSnapshot) -> tuple[list[dict[str, Any]], list[str], bool]:
    """试跑规则并找出问题。返回 (抽取结果, 问题列表, 是否可接受)。"""
    is_api = config.fetch.mode == "api"
    if is_api:
        api_page = _api_page(config, snapshot)
        if api_page is None:
            return [], [f"fetch.url `{config.fetch.url}` 不在页面捕获到的接口中，请使用捕获列表里的接口地址。"], False
        page = api_page
    else:
        page = extract_page(snapshot.html, snapshot.url, config)
    items = page.items
    problems: list[str] = list(page.errors[:5])
    keys = [f.key for f in config.list.fields]
    if not items:
        what = "接口数据" if is_api else "页面"
        problems.append(f"item_selector `{config.list.item_selector}` 在{what}中没有匹配到任何拍品。")
        return items, problems, False

    critical = False
    if len(items) < 2:
        problems.append(f"只匹配到 {len(items)} 个拍品容器，列表页通常有多个拍品，请检查 item_selector 是否过于具体。")
    if "title" not in keys:
        problems.append("缺少 title 字段。")
        critical = True
    if not any(k in keys for k in PRICE_KEYS):
        problems.append("缺少价格字段（current_price / deal_price / start_price）。如页面确实没有价格可忽略。")
    for key in keys:
        empty = sum(1 for it in items if it.get(key) in (None, ""))
        if empty / len(items) > 0.5:
            problems.append(f"字段 {key} 在 {empty}/{len(items)} 个拍品中为空，选择器或 attr 可能不正确。")
            if key == "title" or key in PRICE_KEYS:
                critical = True
    unique = config.list.unique_key or ("url" if "url" in keys else None)
    if unique:
        values = [it.get(unique) for it in items if it.get(unique)]
        if len(set(map(str, values))) < len(values):
            problems.append(f"unique_key 字段 {unique} 存在重复值，无法用于去重。")
    if config.pagination.type == "next_link" and page.next_url is None:
        problems.append(f"翻页选择器 `{config.pagination.selector}` 在页面中没有找到链接，如果没有下一页可将 pagination.type 设为 none。")
    acceptable = not critical and len(items) >= 1
    return items, problems, acceptable


def _score(items: list[dict[str, Any]], acceptable: bool, problems: list[str]) -> tuple:
    return (acceptable, min(len(items), 50) > 0, -len(problems), len(items))


async def generate_rule(
    provider: LLMProvider,
    ctx: FetchContext,
    fetch_mode: str,
    hint: str | None = None,
    max_rounds: int = 3,
    snapshot: PageSnapshot | None = None,
    previous_rule: dict[str, Any] | None = None,
) -> GenerationResult:
    started = time.monotonic()
    result = GenerationResult()
    if snapshot is None:
        snapshot = await capture_page(ctx, fetch_mode, provider.config.supports_vision)

    compressed = compress_html(snapshot.html)
    content = first_message(snapshot.url, compressed, hint, fetch_mode, summarize_json(snapshot.json_responses))
    if previous_rule:
        content += "\n\n该站点原有规则已失效（抽取不到数据），供参考：\n" + json.dumps(previous_rule, ensure_ascii=False)
    messages = [Message("user", content, [snapshot.screenshot] if snapshot.screenshot else [])]
    schema = tool_schema()
    best: tuple | None = None

    for round_no in range(1, max_rounds + 1):
        result.rounds = round_no
        llm = await provider.generate_structured(SYSTEM_PROMPT, messages, schema, TOOL_NAME, TOOL_DESCRIPTION)
        result.prompt_tokens += llm.prompt_tokens
        result.completion_tokens += llm.completion_tokens
        messages.append(Message("assistant", llm.raw_text or json.dumps(llm.data, ensure_ascii=False)))

        raw_rule = llm.data.get("rule", llm.data)
        explanation = str(llm.data.get("explanation", ""))
        try:
            config = RuleConfig.model_validate(raw_rule)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]]
            log.info("AI rule round %s invalid: %s", round_no, errors)
            result.problems = ["规则结构校验失败：" + "；".join(errors)]
            messages.append(Message("user", repair_message(result.problems, [], raw_rule if isinstance(raw_rule, dict) else None)))
            continue

        items, problems, acceptable = evaluate(config, snapshot)
        score = _score(items, acceptable, problems)
        if best is None or score > best[0]:
            best = (score, config, items, explanation, problems, acceptable)
        log.info("AI rule round %s: %d items, problems=%s", round_no, len(items), problems)
        if acceptable and not problems:
            break
        if round_no < max_rounds:
            messages.append(Message("user", repair_message(problems, [{k: jsonable(v) for k, v in it.items()} for it in items])))

    if best:
        _, config, items, explanation, problems, acceptable = best
        result.rule, result.explanation, result.problems, result.ok = config, explanation, problems, acceptable
        result.items = [{k: jsonable(v) for k, v in it.items()} for it in items]
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


__all__ = ["GenerationResult", "LLMError", "PageSnapshot", "capture_page", "evaluate", "generate_rule"]
