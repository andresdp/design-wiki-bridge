"""Run manifests (R10, R11, KTD13, KTD14) and the paired-configuration gate (R6, R7)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Condition = Literal["A", "B"]
WebMode = Literal["record", "replay", "live"]


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Network settings for every OpenAI client: a slow call fails (and is recorded) instead of
# stalling a batch run on the SDK's 10-minute default.
MODEL_NET: dict = {"timeout": 180, "max_retries": 2}


class Models(_Strict):
    generator: str = Field(min_length=1)
    reranker: str = Field(min_length=1)
    embedding: str = Field(min_length=1)
    temperature: float = 0.0


class PromptRef(_Strict):
    path: str
    sha256: str | None = None

    def resolve(self, base_dir: Path) -> PromptRef:
        text = (base_dir / self.path).read_text()
        digest = prompt_hash(text)
        if self.sha256 and self.sha256 != digest:
            raise ValueError(
                f"prompt {self.path} hash mismatch: manifest {self.sha256}, file {digest}"
            )
        return PromptRef(path=self.path, sha256=digest)


class Budgets(_Strict):
    """KTD14 defaults. Repository-branch budgets may differ between A and B (R7)."""

    # repository branch (condition-specific)
    code_top_k: int = 40  # = rerank_pool, so code-only items can fill the token budget
    wiki_search_k: int = 10
    wiki_read_sections: int = 5
    wiki_max_spans: int = 20
    # shared
    max_span_tokens: int = 1500
    pdf_top_k: int = 10
    rerank_pool: int = 40
    rerank_top_n: int = 40  # = pool: the context token budget is the binding limit
    context_tokens: int = 8000


REPO_BUDGET_FIELDS = ("code_top_k", "wiki_search_k", "wiki_read_sections", "wiki_max_spans")
SHARED_BUDGET_FIELDS = tuple(f for f in Budgets.model_fields if f not in REPO_BUDGET_FIELDS)


class QuestionSetRef(_Strict):
    id: str
    path: str
    hash: str | None = None  # None = not frozen (dev sets only)


class WebConfig(_Strict):
    enabled: bool = False
    mode: WebMode = "replay"
    store_id: str | None = None
    max_results: int = 5
    search_depth: Literal["basic", "advanced"] = "basic"
    # The project's own docs/source sites restate (unpinned) code behavior and would let
    # either condition answer without finding the original code; see the spike doc §8.
    exclude_domains: list[str] = Field(default_factory=list)
    # True: web runs only when a shared LLM judge finds the local evidence insufficient
    # (the check_local gate). False: web runs whenever the item allows it.
    conditional_web_search: bool = True

    @property
    def gated(self) -> bool:
        """Web is on and gated by the sufficiency judge."""
        return self.enabled and self.conditional_web_search

    @model_validator(mode="after")
    def _store_needed(self) -> WebConfig:
        if self.enabled and self.mode != "live" and not self.store_id:
            raise ValueError("web.store_id is required when web is enabled in record/replay mode")
        return self


class Indexes(_Strict):
    code_index_id: str | None = None
    pdf_index_id: str | None = None
    wiki_snapshot_id: str | None = None


class RunManifest(_Strict):
    run_id: str = Field(min_length=1)
    condition: Condition
    repo: str
    repo_sha: str = Field(min_length=7)
    checkout: str | None = None
    question_set: QuestionSetRef
    indexes: Indexes = Field(default_factory=Indexes)
    pdf_enabled: bool = True
    models: Models
    prompts: dict[str, PromptRef] = Field(default_factory=dict)
    budgets: Budgets = Field(default_factory=Budgets)
    web: WebConfig = Field(default_factory=WebConfig)
    notes: str = ""

    @model_validator(mode="after")
    def _condition_inputs(self) -> RunManifest:
        if self.condition == "A" and not self.indexes.code_index_id:
            raise ValueError("condition A requires indexes.code_index_id")
        if self.condition == "B" and not self.indexes.wiki_snapshot_id:
            raise ValueError("condition B requires indexes.wiki_snapshot_id")
        if self.pdf_enabled and not self.indexes.pdf_index_id:
            raise ValueError("pdf_enabled requires indexes.pdf_index_id")
        return self

    def run_problems(self) -> list[str]:
        """What stops this manifest from running. Checked at run start, not at load, so
        saved runs from before a requirement existed stay readable by ``evaluate``."""
        if self.web.gated and "sufficiency" not in self.prompts:
            return ["prompts.sufficiency is required when web is enabled and "
                    "web.conditional_web_search is true (add e.g. "
                    "path: src/dwb/prompt_files/sufficiency_v1.md)"]
        return []

    def resolve_prompts(self, base_dir: Path) -> RunManifest:
        """Return a copy with every prompt hash computed and checked against its file."""
        resolved = {k: v.resolve(base_dir) for k, v in self.prompts.items()}
        return self.model_copy(update={"prompts": resolved})

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)


class ManifestError(ValueError):
    pass


def load_manifest(path: str | Path) -> RunManifest:
    data = yaml.safe_load(Path(path).read_text())
    try:
        return RunManifest.model_validate(data)
    except ValidationError as e:
        raise ManifestError(f"invalid manifest {path}:\n{e}") from e


def require_runnable(m: RunManifest) -> None:
    if problems := m.run_problems():
        raise ManifestError(f"manifest {m.run_id} cannot run: " + "; ".join(problems))


class PairedCheck(BaseModel):
    ok: bool
    failures: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _diff(label: str, a: Any, b: Any, out: list[str]) -> None:
    if a != b:
        out.append(f"{label}: A={a!r} B={b!r}")


def check_paired(a: RunManifest, b: RunManifest, *, runnable: bool = False) -> PairedCheck:
    """Fail on any shared-stage difference (R6); only report repository-budget differences (R7).
    ``runnable`` also fails on what stops either manifest from running (``run_problems``);
    evaluation of saved runs leaves it off."""
    failures: list[str] = []
    notes: list[str] = []
    if runnable:
        failures += [f"{m.condition}: {p}" for m in (a, b) for p in m.run_problems()]
    if a.condition == b.condition:
        failures.append(f"both manifests use condition {a.condition}")
    _diff("repo", a.repo, b.repo, failures)
    _diff("repo_sha", a.repo_sha, b.repo_sha, failures)
    _diff("question_set", a.question_set.model_dump(), b.question_set.model_dump(), failures)
    _diff("pdf_enabled", a.pdf_enabled, b.pdf_enabled, failures)
    _diff("indexes.pdf_index_id", a.indexes.pdf_index_id, b.indexes.pdf_index_id, failures)
    _diff("models", a.models.model_dump(), b.models.model_dump(), failures)
    _diff("prompts", a.prompts, b.prompts, failures)
    _diff("web", a.web.model_dump(), b.web.model_dump(), failures)
    for f in SHARED_BUDGET_FIELDS:
        _diff(f"budgets.{f}", getattr(a.budgets, f), getattr(b.budgets, f), failures)
    a_repo = a.budgets.code_top_k if a.condition == "A" else a.budgets.wiki_max_spans
    b_repo = b.budgets.code_top_k if b.condition == "A" else b.budgets.wiki_max_spans
    if a_repo != b_repo:
        notes.append(f"repository candidate budget differs: A={a_repo} B={b_repo} (reported, R7)")
    for f in REPO_BUDGET_FIELDS:
        if getattr(a.budgets, f) != getattr(b.budgets, f):
            notes.append(f"budgets.{f}: A={getattr(a.budgets, f)} B={getattr(b.budgets, f)}")
    return PairedCheck(ok=not failures, failures=failures, notes=notes)
