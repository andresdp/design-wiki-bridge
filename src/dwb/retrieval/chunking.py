"""AST code chunking (KTD5) and span splitting (KTD14).

Python: one chunk per top-level function, per method, and per class header (class line up
to its first method; the whole class when it has no methods). Module-level code between
definitions and non-Python files use 60-line windows overlapping by 10. Every chunk above
``max_span_tokens`` is split by ``split_span`` -- the same helper the wiki resolver uses.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from dataclasses import asdict, dataclass

from dwb.evidence import EvidenceItem, code_item
from dwb.repo import RepoReader
from dwb.tokens import count_tokens, split_lines_by_tokens

LOG = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = (".py", ".pyi", ".rst", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml",
                      ".yml")


@dataclass(frozen=True)
class ChunkingConfig:
    window: int = 60
    overlap: int = 10
    max_span_tokens: int = 1500
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    exclude_prefixes: tuple[str, ...] = ("openwiki/",)

    def config_id(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Span:
    path: str
    start: int
    end: int
    kind: str  # function | method | class | module | window
    symbol: str | None = None


def windows(path: str, start: int, end: int, window: int, overlap: int) -> list[Span]:
    out = []
    step = max(1, window - overlap)
    s = start
    while s <= end:
        e = min(end, s + window - 1)
        out.append(Span(path, s, e, "window"))
        if e == end:
            break
        s += step
    return out


def _def_start(node: ast.AST) -> int:
    decos = getattr(node, "decorator_list", [])
    return min([node.lineno, *[d.lineno for d in decos]])


def python_spans(path: str, source: str, cfg: ChunkingConfig) -> list[Span]:
    lines = source.splitlines()
    n = len(lines)
    if n == 0:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        LOG.warning("syntax error in %s (%s); falling back to windows", path, e)
        return windows(path, 1, n, cfg.window, cfg.overlap)

    spans: list[Span] = []
    covered: list[tuple[int, int]] = []
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, defs):
            s, e = _def_start(node), node.end_lineno
            spans.append(Span(path, s, e, "function", node.name))
            covered.append((s, e))
        elif isinstance(node, ast.ClassDef):
            s, e = _def_start(node), node.end_lineno
            methods = [m for m in node.body if isinstance(m, defs)]
            header_end = (_def_start(methods[0]) - 1) if methods else e
            spans.append(Span(path, s, max(s, header_end), "class", node.name))
            for m in methods:
                spans.append(
                    Span(path, _def_start(m), m.end_lineno, "method", f"{node.name}.{m.name}")
                )
            covered.append((s, e))

    # module-level code outside definitions
    covered.sort()
    cursor = 1
    gaps = []
    for s, e in covered:
        if s > cursor:
            gaps.append((cursor, s - 1))
        cursor = max(cursor, e + 1)
    if cursor <= n:
        gaps.append((cursor, n))
    for s, e in gaps:
        if any(lines[i - 1].strip() for i in range(s, e + 1)):
            spans.extend(windows(path, s, e, cfg.window, cfg.overlap))
    return sorted(spans, key=lambda sp: (sp.start, sp.end))


def split_span(span: Span, lines: list[str], max_tokens: int) -> list[Span]:
    """Split one span into contiguous sub-spans under ``max_tokens`` (KTD14 shared helper)."""
    body = lines[span.start - 1 : span.end]
    if count_tokens("\n".join(body)) <= max_tokens:
        return [span]
    return [
        Span(span.path, s, e, span.kind, span.symbol)
        for s, e in split_lines_by_tokens(body, span.start, max_tokens)
    ]


def file_spans(path: str, text: str, cfg: ChunkingConfig) -> list[Span]:
    if path.endswith((".py", ".pyi")):
        spans = python_spans(path, text, cfg)
    else:
        n = len(text.splitlines())
        spans = windows(path, 1, n, cfg.window, cfg.overlap) if n else []
    lines = text.splitlines()
    out: list[Span] = []
    for sp in spans:
        out.extend(split_span(sp, lines, cfg.max_span_tokens))
    return out


def chunk_repo(reader: RepoReader, cfg: ChunkingConfig) -> list[EvidenceItem]:
    items: dict[str, EvidenceItem] = {}
    for path in reader.list_files():
        if not path.endswith(cfg.extensions) or path.startswith(cfg.exclude_prefixes):
            continue
        text = reader.read(path)
        if text is None:
            continue
        lines = text.splitlines()
        for sp in file_spans(path, text, cfg):
            item = code_item(
                reader.sha, path, sp.start, sp.end, "\n".join(lines[sp.start - 1 : sp.end]),
                chunk_kind=sp.kind, symbol=sp.symbol,
            )
            items.setdefault(item.id, item)
    return list(items.values())
