"""Token counting and the shared span splitter (KTD14).

One fixed tiktoken encoding is used everywhere, so budgets do not depend on whether
tiktoken recognizes the pinned chat model's name.
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken

ENCODING = "o200k_base"


@lru_cache(maxsize=1)
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding(ENCODING)


def count_tokens(text: str) -> int:
    return len(_enc().encode(text, disallowed_special=()))


def split_lines_by_tokens(
    lines: list[str], start_line: int, max_tokens: int
) -> list[tuple[int, int]]:
    """Split ``lines`` (first line numbered ``start_line``) into contiguous ranges whose text
    stays within ``max_tokens``. Ranges cover every line exactly once. A single line longer
    than the cap becomes its own range (lines are never cut).
    """
    if not lines:
        return []
    ranges: list[tuple[int, int]] = []
    cur_start = start_line
    cur_tokens = 0
    for offset, line in enumerate(lines):
        n = count_tokens(line + "\n")
        lineno = start_line + offset
        if cur_tokens and cur_tokens + n > max_tokens:
            ranges.append((cur_start, lineno - 1))
            cur_start, cur_tokens = lineno, 0
        cur_tokens += n
    ranges.append((cur_start, start_line + len(lines) - 1))
    return ranges
