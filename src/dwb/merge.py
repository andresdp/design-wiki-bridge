"""Candidate merging (KTD12).

Drops items with an identical canonical locator, and code spans contained in a larger span
from the same file at the same commit. The larger span survives. Every dropped ID is kept
in the survivor's ``provenance["merged_ids"]`` and in the returned alias map, so an ID
logged before the merge still resolves (R2). Order follows the input order of survivors.
"""

from __future__ import annotations

from dwb.evidence import CodeLocator, EvidenceItem


def _contains(outer: CodeLocator, inner: CodeLocator) -> bool:
    return (
        outer.commit == inner.commit
        and outer.path == inner.path
        and outer.start_line <= inner.start_line
        and inner.end_line <= outer.end_line
    )


def merge_candidates(items: list[EvidenceItem]) -> tuple[list[EvidenceItem], dict[str, str]]:
    survivors: list[EvidenceItem] = []
    by_id: dict[str, EvidenceItem] = {}
    alias: dict[str, str] = {}

    def absorb(survivor: EvidenceItem, dropped: EvidenceItem) -> None:
        merged = survivor.provenance.setdefault("merged_ids", [])
        for did in [dropped.id, *dropped.provenance.get("merged_ids", [])]:
            if did == survivor.id:
                continue
            if did not in merged:
                merged.append(did)
            alias[did] = survivor.id
        branches = survivor.provenance.setdefault("also_from", [])
        b = dropped.provenance.get("branch")
        if b and b != survivor.provenance.get("branch") and b not in branches:
            branches.append(b)

    for item in items:
        item = item.model_copy(deep=True)
        if item.id in by_id:
            absorb(by_id[item.id], item)
            continue
        if isinstance(item.locator, CodeLocator):
            outer = next(
                (s for s in survivors if isinstance(s.locator, CodeLocator)
                 and _contains(s.locator, item.locator)),
                None,
            )
            if outer is not None:
                absorb(outer, item)
                continue
            inner = [s for s in survivors if isinstance(s.locator, CodeLocator)
                     and _contains(item.locator, s.locator)]
            if inner:
                pos = survivors.index(inner[0])
                for s in inner:
                    survivors.remove(s)
                    del by_id[s.id]
                    absorb(item, s)
                survivors.insert(pos, item)
                by_id[item.id] = item
                for k, v in list(alias.items()):
                    if v in {s.id for s in inner}:
                        alias[k] = item.id
                continue
        survivors.append(item)
        by_id[item.id] = item
    return survivors, alias
