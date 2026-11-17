"""Span matching for evidence recall (KTD15).

code: same path and line ranges overlapping by >= ``min_overlap_lines`` (default 1);
      the Jaccard variant requires line-set Jaccard >= ``jaccard_threshold`` (default 0.5).
pdf:  same document and page.
web:  same URL (fragment ignored) and, when the annotation names a passage, token overlap
      between that passage and the retrieved text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag

from dwb.benchmark.schemas import AnnotatedSpan

WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class MatchRules:
    min_overlap_lines: int = 1
    jaccard_threshold: float = 0.5
    web_passage_overlap: float = 0.5


DEFAULT_RULES = MatchRules()


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


def line_jaccard(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = _overlap(a, b)
    union = (a[1] - a[0] + 1) + (b[1] - b[0] + 1) - inter
    return inter / union if union else 0.0


def _norm_url(u: str) -> str:
    return urldefrag(u.strip().rstrip("/"))[0].lower()


def _token_overlap(needle: str, hay: str) -> float:
    n = set(WORD.findall(needle.lower()))
    return len(n & set(WORD.findall(hay.lower()))) / len(n) if n else 0.0


def matches(
    evidence: dict[str, Any], span: AnnotatedSpan, rules: MatchRules = DEFAULT_RULES, *,
    jaccard: bool = False,
) -> bool:
    """``evidence`` is a trace evidence entry: {source_type, locator, text?}."""
    if evidence["source_type"] != span.source_type:
        return False
    loc = evidence["locator"]
    if span.source_type == "code":
        if loc["path"].removeprefix("./") != span.path.removeprefix("./"):
            return False
        a = (loc["start_line"], loc["end_line"])
        b = (span.start_line, span.end_line)
        if jaccard:
            return line_jaccard(a, b) >= rules.jaccard_threshold
        return _overlap(a, b) >= rules.min_overlap_lines
    if span.source_type == "pdf":
        return loc["doc_id"] == span.doc_id and loc["page"] == span.page
    if _norm_url(loc["url"]) != _norm_url(span.url):
        return False
    if not span.passage:
        return True
    return _token_overlap(span.passage, evidence.get("text") or "") >= rules.web_passage_overlap
