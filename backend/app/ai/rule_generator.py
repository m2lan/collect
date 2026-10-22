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
    DETAIL_SYSTEM_PROMPT,
    DETAIL_TOOL_DESCRIPTION,
    DETAIL_TOOL_NAME,
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    detail_message,
    detail_tool_schema,
    first_message,
    repair_message,
    tool_schema,
)
from app.ai.providers import LLMError, LLMProvider, Message
from app.crawler.extractor import PageResult, extract_captured, extract_detail, extract_json, extract_page
from app.crawler.fetchers import FetchContext, open_fetcher
from app.crawler.pipeline import jsonable
from app.crawler.sniff import find_candidates
from app.schemas.rule import DetailRule, FetchConfig, FieldRule, Pagination, RuleConfig

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
    missing_fields: list[str] = field(default_factory=list)

    def add_usage(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion


async def capture_page(ctx: FetchContext, fetch_mode: str, want_screenshot: bool) -> PageSnapshot:
    browser = fetch_mode == "browser"
    fetch = FetchConfig(mode=fetch_mode, wait_ms=1500 if browser else 0, scroll=browser, timeout=45)  # type: ignore[arg-type]
    async with open_fetcher(ctx, fetch, capture_json=browser) as fetcher:
        fetched = await fetcher.fetch(ctx.start_url)
        if browser and hasattr(fetcher, "wait_json_quiet"):
            # 列表接口常常晚于页面加载返回
            fetched.json_responses = await fetcher.wait_json_quiet()  # type: ignore[attr-defined]
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
    if config.fetch.capture:
        page = extract_captured(snapshot.json_responses, snapshot.url, config)
        is_api = True
    elif is_api:
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
        problems.append(
            f"翻页没有生效：翻页选择器 `{config.pagination.selector}` 在页面中没有找到带链接的元素。"
            "如果“下一页”是没有 href 的按钮请用 click，如果没有下一页可将 pagination.type 设为 none。"
        )
    acceptable = not critical and len(items) >= 1
    return items, problems, acceptable


def _score(items: list[dict[str, Any]], acceptable: bool, problems: list[str]) -> tuple:
    return (acceptable, min(len(items), 50) > 0, -len(problems), len(items))


async def check_pagination(ctx: FetchContext, config: RuleConfig, fetch_mode: str) -> list[str]:
    """实际抓取前 2 页，验证翻页规则是否生效。"""
    from app.crawler.runner import RunLog, crawl

    if config.pagination.type == "none":
        return []
    runlog = RunLog()
    test = config.model_copy(update={"fetch": config.fetch.model_copy(update={"mode": config.fetch.mode or fetch_mode})})
    try:
        res = await crawl(ctx, test, rate_limit=2, max_pages=2, runlog=runlog, with_detail=False)
    except Exception as exc:
        return [f"按翻页规则抓取第 2 页失败：{type(exc).__name__}: {str(exc)[:200]}"]
    if res.pages >= 2:
        return []
    reason = runlog.entries[-1]["msg"] if runlog.entries else "未知原因"
    return [f"翻页没有生效（{config.pagination.type}，selector/template：{config.pagination.selector or config.pagination.template}）：{reason}。"
            "请检查页面中的翻页控件：有真实链接用 next_link，链接有页码规律用 url_template，JS 按钮用 click，无限滚动用 fetch.scroll。"]


async def _list_stage(
    provider: LLMProvider, ctx: FetchContext, fetch_mode: str, snapshot: PageSnapshot, hint: str | None,
    max_rounds: int, previous_rule: dict[str, Any] | None, result: GenerationResult,
) -> tuple | None:
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
        result.add_usage(llm.prompt_tokens, llm.completion_tokens)
        messages.append(Message("assistant", llm.raw_text or json.dumps(llm.data, ensure_ascii=False)))

        raw_rule = llm.data.get("rule", llm.data)
        explanation = str(llm.data.get("explanation", ""))
        missing = [str(m) for m in (llm.data.get("missing_fields") or []) if str(m).strip()]
        try:
            config = RuleConfig.model_validate(raw_rule)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]]
            log.info("AI rule round %s invalid: %s", round_no, errors)
            result.problems = ["规则结构校验失败：" + "；".join(errors)]
            messages.append(Message("user", repair_message(result.problems, [], raw_rule if isinstance(raw_rule, dict) else None)))
            continue

        items, problems, acceptable = evaluate(config, snapshot)
        if acceptable and not problems:
            # 首页没问题时再实际翻页验证
            problems = await check_pagination(ctx, config, fetch_mode)
        score = _score(items, acceptable, problems)
        if best is None or score > best[0]:
            best = (score, config, items, explanation, problems, acceptable, missing)
        log.info("AI rule round %s: %d items, problems=%s", round_no, len(items), problems)
        if acceptable and not problems:
            break
        if round_no < max_rounds:
            messages.append(Message("user", repair_message(problems, [{k: jsonable(v) for k, v in it.items()} for it in items])))
    return best


async def fetch_detail_pages(ctx: FetchContext, fetch_mode: str, urls: list[str]) -> list[PageSnapshot]:
    browser = fetch_mode == "browser"
    fetch = FetchConfig(mode="browser" if browser else "static", wait_ms=1000 if browser else 0, timeout=45)  # type: ignore[arg-type]
    pages = []
    async with open_fetcher(ctx, fetch) as fetcher:
        for url in urls:
            try:
                f = await fetcher.fetch(url)
            except Exception as exc:
                log.info("detail page %s failed: %s", url, exc)
                continue
            pages.append(PageSnapshot(f.url, f.html))
    return pages


def evaluate_detail(fields: list[FieldRule], pages: list[PageSnapshot]) -> tuple[list[dict[str, Any]], list[str]]:
    detail = DetailRule(enabled=True, fields=fields)
    values, problems = [], []
    for page in pages:
        v, errors = extract_detail(page.html, page.url, detail)
        values.append(v)
        problems += errors[:3]
    for f in fields:
        empty = sum(1 for v in values if v.get(f.key) in (None, ""))
        if empty:
            problems.append(f"字段 {f.key} 在 {empty}/{len(values)} 个详情页中为空，请检查选择器或改用 regex 从整页文本匹配。")
    return values, problems


async def _detail_stage(
    provider: LLMProvider, ctx: FetchContext, fetch_mode: str, config: RuleConfig, items: list[dict[str, Any]],
    wanted: list[str], hint: str | None, max_rounds: int, result: GenerationResult,
) -> tuple[DetailRule | None, list[dict[str, Any]], str, list[str]]:
    url_field = "url" if any(it.get("url") for it in items) else next(
        (f.key for f in config.list.fields if f.key.endswith("url") and any(it.get(f.key) for it in items)), None
    )
    if not url_field:
        return None, [], "", ["列表规则中没有详情页链接字段（url），无法分析详情页。"]
    urls = list(dict.fromkeys(str(it[url_field]) for it in items if it.get(url_field)))[:2]
    pages = await fetch_detail_pages(ctx, fetch_mode, urls)
    if not pages:
        return None, [], "", ["详情页打开失败，无法生成详情页规则。"]

    messages = [Message("user", detail_message(pages[0].url, compress_html(pages[0].html, 40_000), wanted, hint))]
    schema = detail_tool_schema()
    best_score: tuple | None = None
    best_fields: list[FieldRule] = []
    best_values: list[dict[str, Any]] = []
    explanation, problems = "", ["未能生成详情页规则。"]
    for round_no in range(1, max_rounds + 1):
        llm = await provider.generate_structured(DETAIL_SYSTEM_PROMPT, messages, schema, DETAIL_TOOL_NAME, DETAIL_TOOL_DESCRIPTION)
        result.add_usage(llm.prompt_tokens, llm.completion_tokens)
        messages.append(Message("assistant", llm.raw_text or json.dumps(llm.data, ensure_ascii=False)))
        round_expl = str(llm.data.get("explanation", ""))
        try:
            fields = [FieldRule.model_validate(f) for f in llm.data.get("fields") or []]
            if any(f.type == "jsonpath" for f in fields):
                raise ValueError("详情页字段不能使用 jsonpath")
        except (ValidationError, ValueError) as exc:
            messages.append(Message("user", repair_message([f"字段规则校验失败：{str(exc)[:500]}"], [])))
            continue
        if not fields:
            if best_score is None:
                explanation, problems = round_expl, ["AI 认为详情页上也没有所需字段。"]
            break
        values, round_problems = evaluate_detail(fields, pages)
        score = (-len(round_problems), len(fields))
        if best_score is None or score > best_score:
            best_score, best_fields, best_values = score, fields, values
            explanation, problems = round_expl, round_problems
        log.info("AI detail round %s: problems=%s", round_no, round_problems)
        if not round_problems:
            break
        if round_no < max_rounds:
            messages.append(Message("user", repair_message(round_problems, [{k: jsonable(x) for k, x in v.items()} for v in values])))
    if not best_fields:
        return None, [], explanation, problems
    return DetailRule(enabled=True, url_field=url_field, fields=best_fields), best_values, explanation, problems


async def generate_rule(
    provider: LLMProvider,
    ctx: FetchContext,
    fetch_mode: str,
    hint: str | None = None,
    max_rounds: int = 3,
    snapshot: PageSnapshot | None = None,
    previous_rule: dict[str, Any] | None = None,
    analyze_detail: bool = False,
) -> GenerationResult:
    started = time.monotonic()
    result = GenerationResult()
    if snapshot is None:
        snapshot = await capture_page(ctx, fetch_mode, provider.config.supports_vision)

    best = await _list_stage(provider, ctx, fetch_mode, snapshot, hint, max_rounds, previous_rule, result)
    if best:
        _, config, items, explanation, problems, acceptable, missing = best
        pagination_failed = any(p.startswith(("翻页没有生效", "按翻页规则")) for p in problems)
        if pagination_failed and acceptable:
            # 列表本身可用，只是翻页配置不对：退回只抓第一页，保证规则能用
            config = config.model_copy(update={"pagination": Pagination()})
            problems = [p for p in problems if not p.startswith(("翻页没有生效", "按翻页规则"))]
            problems.append("翻页未能自动配置成功，已设为只抓第一页；可用可视化选取的“选择下一页”或手动配置。")
        result.rule, result.explanation, result.problems, result.ok = config, explanation, problems, acceptable
        result.missing_fields = missing
        result.items = [{k: jsonable(v) for k, v in it.items()} for it in items]

        if acceptable and (analyze_detail or missing):
            detail, values, detail_expl, detail_problems = await _detail_stage(
                provider, ctx, fetch_mode, config, items, missing, hint, max_rounds, result
            )
            if detail:
                result.rule = config.model_copy(update={"detail": detail})
                for item, v in zip(result.items, values):
                    item.update({k: jsonable(x) for k, x in v.items() if x is not None})
            if detail_expl:
                result.explanation += "\n\n【详情页】" + detail_expl
            result.problems += [f"详情页：{p}" for p in detail_problems]
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


__all__ = ["GenerationResult", "LLMError", "PageSnapshot", "capture_page", "evaluate", "generate_rule"]
