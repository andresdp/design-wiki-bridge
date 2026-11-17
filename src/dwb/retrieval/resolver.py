"""Wiki reference resolver: read sections -> original code spans at the pinned SHA (U8).

KTD4 as revised after the U1 spike (user-approved 2026-11-17). OpenWiki claims are
page-level, so a section's evidence is collected in this order:

1. ``inline_link``  -- line-range links in the section text (``../../src/x.py#L10-L20``);
2. ``claim_section`` -- page claims whose statement matches this section (token overlap
   >= 0.5; claims within 0.15 of their best section count for each of those sections);
3. ``claim_page``   -- page claims that match no section;
4. ``ast``/``path`` -- only if 1-3 gave nothing: backticked dotted symbols resolved via
   ``ast`` to their def/class lines, and repository paths named without lines (whole file).

Every span is validated against ``git show <sha>:<path>`` and split with the shared
KTD14 helper. Failed references are logged as unresolved with their raw text.
"""

from __future__ import annotations

import ast
import keyword
import posixpath
import re
from dataclasses import dataclass

from dwb.evidence import EvidenceItem, code_item
from dwb.repo import RepoReader
from dwb.retrieval.chunking import Span, split_span
from dwb.retrieval.wiki_types import ResolveResult, Section, UnresolvedRef

REPO_RES = re.compile(r"^repo://([^#]+)(?:#L(\d+)-L(\d+))?$")  # whole-file form seen in 0.9.0
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
BACKTICK = re.compile(r"`([^`\n]{2,120})`")
DOTTED = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*(\(\))?$")
PATHLIKE = re.compile(r"^[\w.\-/]+\.(py|pyi|rst|md|toml|cfg|txt|ya?ml)$")
WORD = re.compile(r"[a-z_][a-z0-9_]{2,}")
STOP = set("the and for that with this from are its into when then than which also only not but "
           "use uses used can each one two all any via per was has have their there these those "
           "they them been being will would should could".split())
SKIP_SYMBOLS = set(dir(__builtins__)) | set(keyword.kwlist) | {"self", "cls", "True", "False",
                                                              "None"}
METHOD_ORDER = {"inline_link": 0, "claim_section": 1, "claim_page": 2, "ast": 3, "path": 3}


def _tokens(s: str) -> set[str]:
    return {t for t in WORD.findall(s.lower()) if t not in STOP}


def overlap(statement: str, text: str) -> float:
    st = _tokens(statement)
    return len(st & _tokens(text)) / len(st) if st else 0.0


def attribute_claims(claims: list[dict], page_sections: list[str], *, threshold: float = 0.5,
                     margin: float = 0.15) -> dict[str, set[int]]:
    """claim id -> indices of the sections it belongs to (empty set = matches none)."""
    out: dict[str, set[int]] = {}
    for c in claims:
        scores = [overlap(c.get("statement", ""), s) for s in page_sections]
        best = max(scores, default=0.0)
        out[c["id"]] = ({i for i, s in enumerate(scores) if s >= best - margin}
                        if best >= threshold else set())
    return out


@dataclass(frozen=True)
class SymbolDef:
    qualname: str  # dotted module path + class/function names
    path: str
    start: int
    end: int


def _module_name(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    p = p.removeprefix("src/")
    parts = [x for x in p.split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class SymbolIndex:
    def __init__(self, reader: RepoReader):
        self.defs: list[SymbolDef] = []
        for path in reader.list_files():
            if not path.endswith(".py"):
                continue
            src = reader.read(path) or ""
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            mod = _module_name(path)
            self._walk(tree.body, mod, path)

    def _walk(self, body, prefix: str, path: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
                q = f"{prefix}.{node.name}" if prefix else node.name
                self.defs.append(SymbolDef(q, path, start, node.end_lineno))
                if isinstance(node, ast.ClassDef):
                    self._walk(node.body, q, path)

    def lookup(self, ref: str) -> list[SymbolDef]:
        ref = ref.removesuffix("()")
        exact = [d for d in self.defs if d.qualname == ref]
        if exact:
            return exact
        return [d for d in self.defs if d.qualname.endswith("." + ref)]


class WikiResolver:
    def __init__(self, reader: RepoReader, *, max_span_tokens: int = 1500,
                 max_symbol_matches: int = 3):
        self.reader = reader
        self.max_span_tokens = max_span_tokens
        self.max_symbol_matches = max_symbol_matches
        self._symbols: SymbolIndex | None = None

    @property
    def symbols(self) -> SymbolIndex:
        if self._symbols is None:
            self._symbols = SymbolIndex(self.reader)
        return self._symbols

    # -- helpers ----------------------------------------------------------------------

    def _span(self, path: str, a: int, b: int, method: str, sec: Section, order: int,
              unresolved: list[UnresolvedRef], raw: str, **prov) -> list[EvidenceItem]:
        lines = self.reader.lines(path)
        if lines is None:
            unresolved.append(UnresolvedRef(section=sec.ref.ref, raw=raw,
                                            reason=f"{path} not at {self.reader.sha[:12]}"))
            return []
        if a < 1 or b > len(lines) or a > b:
            unresolved.append(UnresolvedRef(section=sec.ref.ref, raw=raw,
                                            reason=f"lines {a}-{b} outside 1-{len(lines)}"))
            return []
        items = []
        for sp in split_span(Span(path, a, b, method), lines, self.max_span_tokens):
            items.append(code_item(
                self.reader.sha, path, sp.start, sp.end, "\n".join(lines[sp.start - 1 : sp.end]),
                branch="wiki", rank=sec.ref.rank, resolution_method=method,
                wiki_ref=sec.ref.ref, ref_order=order, **prov,
            ))
        return items

    def _link_targets(self, sec: Section) -> list[tuple[str, str]]:
        base = posixpath.dirname(sec.ref.page)
        out = []
        for target in MD_LINK.findall(sec.text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path, _, frag = target.partition("#")
            rp = posixpath.normpath(posixpath.join(base, path))
            if rp.startswith(("openwiki/", "../")) or rp == "openwiki":
                continue
            out.append((rp, frag))
        return out

    # -- main -------------------------------------------------------------------------

    def resolve_section(self, sec: Section) -> tuple[list[EvidenceItem], list[UnresolvedRef],
                                                     list[dict]]:
        items: list[EvidenceItem] = []
        unresolved: list[UnresolvedRef] = []
        ambiguous: list[dict] = []
        order = 0

        # 1. inline line-range links
        path_only: list[str] = []
        for rp, frag in self._link_targets(sec):
            m = re.match(r"^L(\d+)(?:-L(\d+))?$", frag)
            if m:
                a, b = int(m[1]), int(m[2] or m[1])
                items += self._span(rp, a, b, "inline_link", sec, order, unresolved,
                                    f"{rp}#{frag}")
                order += 1
            else:
                path_only.append(rp)

        # 2-3. page claims attributed to this section, then unmatched page claims
        claims = sec.raw.get("page_claims", [])
        page_sections = sec.raw.get("page_sections") or []
        idx = sec.raw.get("section_index")
        if idx is None:  # section text not found in the page split: score it on its own
            page_sections, idx = [*page_sections, sec.text], len(page_sections)
        attribution = attribute_claims(claims, page_sections)
        for method in ("claim_section", "claim_page"):
            for c in claims:
                groups = attribution.get(c["id"], set())
                selected = (idx in groups) if method == "claim_section" else not groups
                if not selected:
                    continue
                if method == "claim_section" and len(groups) > 1:
                    ambiguous.append({"section": sec.ref.ref, "claim": c["id"],
                                      "kind": "claim_sections", "sections": sorted(groups)})
                for res in c.get("evidence", []):
                    m = REPO_RES.match(res)
                    if not m:
                        unresolved.append(UnresolvedRef(section=sec.ref.ref, raw=res,
                                                        reason="not a repo:// resource"))
                        continue
                    if m[2] is None:  # whole-file evidence
                        a, b = 1, self.reader.line_count(m[1]) or 1
                    else:
                        a, b = int(m[2]), int(m[3])
                    items += self._span(m[1], a, b, method, sec, order, unresolved, res,
                                        claim_id=c["id"], whole_file=m[2] is None)
                    order += 1

        # 4. AST / path fallback, only when nothing above resolved
        if not items:
            refs = [r.strip() for r in BACKTICK.findall(sec.text)] + path_only
            seen: set[str] = set()
            for ref in refs:
                if ref in seen:
                    continue
                seen.add(ref)
                if PATHLIKE.match(ref) or "/" in ref:
                    path = ref.removeprefix("./")
                    candidates = [path] if self.reader.exists(path) else [
                        f for f in self.reader.list_files() if f.endswith("/" + path)]
                    if len(candidates) == 1:
                        n = self.reader.line_count(candidates[0]) or 1
                        items += self._span(candidates[0], 1, n, "path", sec, order, unresolved,
                                            ref)
                        order += 1
                    else:
                        unresolved.append(UnresolvedRef(
                            section=sec.ref.ref, raw=ref,
                            reason="no such path" if not candidates else "ambiguous path"))
                    continue
                if not DOTTED.match(ref) or ref.removesuffix("()") in SKIP_SYMBOLS:
                    continue
                defs = self.symbols.lookup(ref)
                if not defs:
                    unresolved.append(UnresolvedRef(section=sec.ref.ref, raw=ref,
                                                    reason="no matching definition"))
                    continue
                if len(defs) > 1:
                    ambiguous.append({"section": sec.ref.ref, "symbol": ref, "kind": "symbol",
                                      "definitions": [f"{d.path}:{d.start}" for d in defs]})
                for d in defs[: self.max_symbol_matches]:
                    items += self._span(d.path, d.start, d.end, "ast", sec, order, unresolved,
                                        ref, symbol=d.qualname)
                order += 1
        if not items and not unresolved:
            unresolved.append(UnresolvedRef(section=sec.ref.ref, raw=sec.text[:200],
                                            reason="no claims or recognizable references"))
        return items, unresolved, ambiguous

    def resolve(self, sections: list[Section], max_spans: int) -> ResolveResult:
        all_items: list[EvidenceItem] = []
        result = ResolveResult()
        for sec in sorted(sections, key=lambda s: s.ref.rank):
            items, unresolved, ambiguous = self.resolve_section(sec)
            all_items += items
            result.unresolved += unresolved
            result.ambiguous += ambiguous
        all_items.sort(key=lambda i: (i.provenance["rank"],
                                      METHOD_ORDER[i.provenance["resolution_method"]],
                                      i.provenance["ref_order"], i.locator.start_line))
        seen: set[str] = set()
        for it in all_items:
            if it.id in seen:
                continue
            seen.add(it.id)
            result.items.append(it)
        result.items = result.items[:max_spans]
        return result
