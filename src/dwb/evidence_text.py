"""Restore code passage text that traces store by reference.

Traces keep code evidence as (commit, path, start_line, end_line) only. The text is exactly
those lines of ``git show <commit>:<path>``, which is how the retrievers and the resolver
produced it, so it can be read back losslessly from the pinned checkout.
"""

from __future__ import annotations

from pathlib import Path

from dwb.repo import RepoReader
from dwb.trace import TraceRecord


class EvidenceTextError(RuntimeError):
    pass


def hydrate(traces: list[TraceRecord], checkout: str | Path | None) -> int:
    """Fill missing text for code passages in each trace's final context. Returns how many
    passages were filled. Traces that already carry the text are left unchanged."""
    readers: dict[str, RepoReader] = {}
    filled = 0
    for t in traces:
        for eid in t.final_context_ids:
            ev = t.evidence.get(eid)
            if not ev or ev.get("source_type") != "code" or "text" in ev:
                continue
            loc = ev["locator"]
            if checkout is None or not Path(checkout).exists():
                raise EvidenceTextError(
                    f"code passages are stored by reference; their text needs the checkout "
                    f"{checkout or '(manifest has no checkout)'} at commit {loc['commit'][:12]}")
            commit = loc["commit"]
            if commit not in readers:
                try:
                    readers[commit] = RepoReader.from_git(checkout, commit)
                except Exception as e:
                    raise EvidenceTextError(f"commit {commit[:12]} is not in {checkout}: "
                                            f"{e}") from None
            text = readers[commit].span_text(loc["path"], loc["start_line"], loc["end_line"])
            if text is None:
                raise EvidenceTextError(f"{loc['path']}:{loc['start_line']}-{loc['end_line']} "
                                        f"is not readable at {commit[:12]} in {checkout}")
            ev["text"] = text
            filled += 1
    return filled
