"""Evidence recall at the candidate stage and in the final context (R14, KTD15)."""

from __future__ import annotations

from dataclasses import dataclass

from dwb.benchmark.schemas import QuestionRecord
from dwb.eval.matching import DEFAULT_RULES, MatchRules, matches
from dwb.trace import TraceRecord

CANDIDATE_KEYS = ("repo", "pdf", "web")


@dataclass
class RecallResult:
    candidates: float | None
    final: float | None
    candidates_jaccard: float | None
    final_jaccard: float | None
    n_requirements: int
    satisfied_final: list[str]


def _satisfied(record: QuestionRecord, trace: TraceRecord, ids: list[str], rules: MatchRules,
               jaccard: bool) -> list[str]:
    ev = [trace.evidence[i] for i in ids if i in trace.evidence]
    return [
        req.id for req in record.evidence
        if any(matches(e, alt, rules, jaccard=jaccard) for alt in req.alternatives for e in ev)
    ]


def recall(record: QuestionRecord, trace: TraceRecord,
           rules: MatchRules = DEFAULT_RULES) -> RecallResult:
    """Share of evidence requirements met by any acceptable alternative. ``None`` when the
    record has no annotated evidence (unannotated public items)."""
    n = len(record.evidence)
    cand_ids = list(dict.fromkeys(i for k in CANDIDATE_KEYS for i in trace.candidates.get(k, [])))
    final_ids = trace.final_context_ids
    if n == 0:
        return RecallResult(None, None, None, None, 0, [])

    def share(ids: list[str], jac: bool) -> float:
        return len(_satisfied(record, trace, ids, rules, jac)) / n

    return RecallResult(
        candidates=share(cand_ids, False),
        final=share(final_ids, False),
        candidates_jaccard=share(cand_ids, True),
        final_jaccard=share(final_ids, True),
        n_requirements=n,
        satisfied_final=_satisfied(record, trace, final_ids, rules, False),
    )
