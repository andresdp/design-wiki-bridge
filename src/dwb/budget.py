"""Context assembly under the KTD14 token budget with per-prompt aliases (KTD1)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dwb.evidence import CodeLocator, EvidenceItem, PdfLocator
from dwb.tokens import count_tokens


def format_item(alias: str, item: EvidenceItem) -> str:
    loc = item.locator
    if isinstance(loc, CodeLocator):
        head = f"[{alias}] code {loc.path} lines {loc.start_line}-{loc.end_line}"
    elif isinstance(loc, PdfLocator):
        head = f"[{alias}] pdf {loc.doc_id} ({loc.doc_version}) page {loc.page}"
    else:
        head = f"[{alias}] web {loc.url} (passage {loc.passage_index})"
    return f"{head}\n```\n{item.text}\n```"


@dataclass
class Context:
    items: list[EvidenceItem]
    aliases: dict[str, str]  # E1 -> stable id
    text: str
    tokens: int
    excluded_ids: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.items]

    @property
    def empty(self) -> bool:
        return not self.items


def assemble_context(ranked: list[EvidenceItem], budget_tokens: int) -> Context:
    """Fill the budget in reranked order. Stops at the first item that does not fit, so the
    included set is always a prefix of the ranking and items are never cut."""
    blocks: list[str] = []
    included: list[EvidenceItem] = []
    used = 0
    for idx, item in enumerate(ranked):
        block = format_item(f"E{len(included) + 1}", item)
        n = count_tokens(block) + 2
        if used + n > budget_tokens:
            excluded = [i.id for i in ranked[idx:]]
            break
        blocks.append(block)
        included.append(item)
        used += n
    else:
        excluded = []
    aliases = {f"E{k}": it.id for k, it in enumerate(included, start=1)}
    return Context(items=included, aliases=aliases, text="\n\n".join(blocks), tokens=used,
                   excluded_ids=excluded)
