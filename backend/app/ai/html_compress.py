"""把渲染后的页面压缩成适合发给 LLM 的精简 HTML。

- 删除脚本、样式、SVG 等与结构无关的内容，只保留有助于写选择器的属性
- 重复的兄弟结构（候选拍品列表）只保留前几个样本，其余折叠为注释
- 截断超长文本与整体长度
"""

import re
from collections import Counter

from lxml import etree, html

DROP_TAGS = {"script", "style", "noscript", "svg", "iframe", "link", "meta", "template", "canvas", "video", "audio", "head"}
KEEP_ATTRS = {"class", "id", "href", "src", "alt", "title", "name", "type", "value", "role", "datetime", "itemprop"}
HIDDEN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
MAX_TEXT = 120
SAMPLES = 3


def _signature(el: etree._Element) -> str:
    cls = " ".join(sorted((el.get("class") or "").split()))
    return f"{el.tag}.{cls}"


def _clean(root: etree._Element) -> None:
    for el in list(root.iter()):
        if el is root:
            continue
        if not isinstance(el.tag, str):  # 注释、处理指令
            el.drop_tree() if hasattr(el, "drop_tree") else el.getparent().remove(el)
            continue
        if el.tag in DROP_TAGS or el.get("hidden") is not None or HIDDEN.search(el.get("style") or ""):
            el.drop_tree()
            continue
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            value = el.attrib[attr]
            if attr in KEEP_ATTRS or attr.startswith("data-") or attr.startswith("aria-label"):
                if attr == "src" and value.startswith("data:"):
                    el.attrib[attr] = "data:..."
                elif len(value) > 200:
                    el.attrib[attr] = value[:200] + "..."
            else:
                del el.attrib[attr]
        if el.text and len(el.text.strip()) > MAX_TEXT:
            el.text = el.text.strip()[:MAX_TEXT] + "…"
        if el.tail and len(el.tail.strip()) > MAX_TEXT:
            el.tail = el.tail.strip()[:MAX_TEXT] + "…"


def _collapse(root: etree._Element) -> None:
    for parent in list(root.iter()):
        if not isinstance(parent.tag, str):
            continue
        children = [c for c in parent if isinstance(c.tag, str)]
        if len(children) <= SAMPLES + 1:
            continue
        counts = Counter(_signature(c) for c in children)
        for sig, n in counts.items():
            if n <= SAMPLES + 1:
                continue
            same = [c for c in children if _signature(c) == sig]
            last_kept = same[SAMPLES - 1]
            for extra in same[SAMPLES:]:
                parent.remove(extra)
            idx = list(parent).index(last_kept)
            label = same[0].tag + "".join(f".{c}" for c in (same[0].get("class") or "").split())
            comment = etree.Comment(f" 另有 {n - SAMPLES} 个相同结构的 <{label}> 元素已省略 ")
            comment.tail = last_kept.tail
            last_kept.tail = None
            parent.insert(idx + 1, comment)


def _remove_empty(root: etree._Element) -> None:
    changed = True
    while changed:
        changed = False
        for el in list(root.iter()):
            if el is root or not isinstance(el.tag, str) or el.tag in {"img", "input", "a", "br"}:
                continue
            if len(el) == 0 and not (el.text or "").strip() and not el.attrib.get("id"):
                if not any(k.startswith("data-") for k in el.attrib):
                    el.drop_tree()
                    changed = True


def compress_html(source: str, max_chars: int = 60_000) -> str:
    try:
        doc = html.fromstring(source)
    except (etree.ParserError, ValueError):
        return source[:max_chars]
    body = doc.find("body")
    root = body if body is not None else doc
    _clean(root)
    _remove_empty(root)
    _collapse(root)
    text = etree.tostring(root, encoding="unicode", method="html")
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n<!-- 页面过长，已截断 -->"
    return text
