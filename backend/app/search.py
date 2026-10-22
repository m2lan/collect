"""全文搜索：多关键词（空格分隔，AND）、同义词扩展（OR），同时匹配标题、编号、扩展信息与 AI 标签。"""

import re
from typing import Any

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models import Lot


def parse_synonyms(text: str | list[str] | None) -> list[set[str]]:
    """同义词配置：每行一组，用逗号/顿号/空格分隔，如 “翡翠,玉翠,缅玉”。"""
    lines = text if isinstance(text, list) else (text or "").splitlines()
    groups = []
    for line in lines:
        words = {w.strip().lower() for w in re.split(r"[,，、\s]+", line) if w.strip()}
        if len(words) > 1:
            groups.append(words)
    return groups


def expand(term: str, groups: list[set[str]]) -> set[str]:
    t = term.lower()
    out = {t}
    for g in groups:
        if t in g:
            out |= g
    return out


def search_condition(query: str, synonyms: list[set[str]] | None = None) -> ColumnElement[bool] | None:
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return None
    columns: list[Any] = [Lot.title, Lot.unique_key, cast(Lot.extra, String), cast(Lot.tags, String)]
    clauses = []
    for term in terms:
        variants = expand(term, synonyms or [])
        clauses.append(or_(*(col.ilike(f"%{v}%") for v in variants for col in columns)))
    return and_(*clauses)


def highlight_terms(query: str, synonyms: list[set[str]] | None = None) -> list[str]:
    terms: set[str] = set()
    for t in re.split(r"\s+", query.strip()):
        if t:
            terms |= expand(t, synonyms or [])
    return sorted(terms, key=len, reverse=True)
