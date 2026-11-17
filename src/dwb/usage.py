"""Token accounting for stage records (RQ3), using LangChain's own facilities.

- LLM calls: ``UsageMetadataCallbackHandler`` collects the provider-reported usage.
  When a model reports none, the stage falls back to LangChain's
  ``count_tokens_approximately`` and is labelled ``approximate``.
- Embedding calls: LangChain embeddings emit no callbacks, so the embedded text is
  counted with ``count_tokens_approximately``.

The context budget and span splitting (KTD14) keep their own exact counter in
``dwb.tokens``: those are pipeline rules, not telemetry, and must stay stable.
A broader telemetry refactor is parked in docs/plans/ (organic telemetry).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately


def approx_tokens(texts: str | Iterable[str]) -> int:
    texts = [texts] if isinstance(texts, str) else list(texts)
    return count_tokens_approximately([HumanMessage(t) for t in texts]) if texts else 0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    source: str = "none"  # provider | approximate | none
    models: tuple[str, ...] = ()

    def extra(self) -> dict[str, Any]:
        return {"tokens_source": self.source, "usage_models": list(self.models)}


def llm_usage(handler: UsageMetadataCallbackHandler,
              prompt: list[BaseMessage] | None = None, output_text: str | None = None) -> Usage:
    """Provider usage from the callback handler, else an approximate count."""
    meta = handler.usage_metadata
    if meta:
        return Usage(input_tokens=sum(u.get("input_tokens", 0) for u in meta.values()),
                     output_tokens=sum(u.get("output_tokens", 0) for u in meta.values()),
                     source="provider", models=tuple(sorted(meta)))
    if prompt is None and output_text is None:
        return Usage()
    return Usage(input_tokens=count_tokens_approximately(prompt) if prompt else 0,
                 output_tokens=approx_tokens(output_text) if output_text else 0,
                 source="approximate")


def embedding_usage(text: str, model: str | None) -> Usage:
    return Usage(input_tokens=approx_tokens(text), source="approximate",
                 models=(model,) if model else ())
