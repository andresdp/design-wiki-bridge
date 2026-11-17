"""Claim correctness and cited-span support with a pinned judge model (KTD16).

Every verdict is cached by (trace key, judged text, prompt hash), so re-evaluating the
same traces makes no judge calls. Every verdict is also appended to a judge log for audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from dwb.benchmark.schemas import QuestionRecord
from dwb.prompts import load_prompt
from dwb.trace import TraceRecord

SCORES = {"supported": 1.0, "partial": 0.5, "missing": 0.0}


class ClaimVerdict(BaseModel):
    """Whether the answer states the required claim."""

    verdict: Literal["supported", "partial", "missing"]
    rationale: str = Field(default="", description="One sentence.")


class SupportVerdict(BaseModel):
    """Whether the cited passages jointly support the claim."""

    supported: bool
    rationale: str = Field(default="", description="One sentence.")


class JudgeCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    self.data[d["key"]] = d["value"]

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, value: dict) -> None:
        self.data[key] = value
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps({"key": key, "value": value}) + "\n")


@dataclass
class JudgeStats:
    calls: int = 0
    cache_hits: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)


class ClaimsJudge:
    def __init__(self, llm: BaseChatModel, prompt_dir: Path, *, model_id: str,
                 cache_path: Path | None = None):
        claim_tpl, self.claim_hash = load_prompt(prompt_dir / "judge_claim_v1.md")
        support_tpl, self.support_hash = load_prompt(prompt_dir / "judge_support_v1.md")
        self._claim = claim_tpl | llm.with_structured_output(ClaimVerdict)
        self._support = support_tpl | llm.with_structured_output(SupportVerdict)
        self.model_id = model_id
        self.cache = JudgeCache(cache_path)
        self.stats = JudgeStats()

    def _key(self, kind: str, trace_key: str, text: str, prompt: str) -> str:
        return hashlib.sha256(json.dumps([kind, trace_key, text, prompt]).encode()).hexdigest()

    def _call(self, kind: str, trace_key: str, judged: str, chain, inputs: dict,
              prompt: str) -> dict:
        key = self._key(kind, trace_key, judged + json.dumps(inputs, sort_keys=True), prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.stats.cache_hits += 1
            value = hit
        else:
            self.stats.calls += 1
            value = chain.invoke(inputs).model_dump()
            self.cache.put(key, value)
        self.stats.log.append({"kind": kind, "trace": trace_key, "judged": judged, **value,
                               "cached": hit is not None})
        return value

    def correctness(self, record: QuestionRecord, trace: TraceRecord) -> dict[str, Any]:
        trace_key = f"{trace.run_id}:{trace.question_id}"
        if record.expected_claims:
            required = [(c.id, c.text, c.weight) for c in record.expected_claims if c.material]
        elif record.reference_answer:
            required = [("reference", "The main points of this verified reference answer:\n"
                         + record.reference_answer, 1.0)]
        else:
            return {"score": None, "verdicts": {}}
        answer = "\n".join(f"- {c.text}" for c in trace.claims)
        verdicts = {}
        for cid, text, _w in required:
            if not trace.claims:
                verdicts[cid] = "missing"
                continue
            v = self._call("claim", trace_key, text, self._claim,
                           {"required": text, "rules": record.partial_credit, "answer": answer},
                           self.claim_hash)
            verdicts[cid] = v["verdict"]
        total = sum(w for _c, _t, w in required)
        score = sum(SCORES[verdicts[c]] * w for c, _t, w in required) / total if total else None
        return {"score": score, "verdicts": verdicts}

    def support(self, trace: TraceRecord) -> dict[str, Any]:
        """Citation support (cited claims supported) and coverage (all claims supported)."""
        trace_key = f"{trace.run_id}:{trace.question_id}"
        per_claim = []
        for c in trace.claims:
            if not c.evidence_ids:
                per_claim.append(False)
                continue
            passages = "\n\n".join(
                f"[{i}]\n{trace.evidence.get(i, {}).get('text', '(passage not in trace)')}"
                for i in c.evidence_ids
            )
            v = self._call("support", trace_key, c.text, self._support,
                           {"claim": c.text, "passages": passages}, self.support_hash)
            per_claim.append(bool(v["supported"]))
        cited = [ok for c, ok in zip(trace.claims, per_claim, strict=True) if c.evidence_ids]
        return {
            "citation_support": (sum(cited) / len(cited)) if cited else None,
            "coverage": (sum(per_claim) / len(per_claim)) if per_claim else None,
            "n_claims": len(per_claim),
            "n_cited": len(cited),
        }
