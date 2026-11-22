from pathlib import Path

import pytest
import yaml

from dwb.manifest import (
    ManifestError,
    PromptRef,
    check_paired,
    load_manifest,
    prompt_hash,
    require_runnable,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "configs" / "manifests"


def _write(tmp_path, data, name="m.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


@pytest.fixture
def base():
    return yaml.safe_load((EXAMPLES / "flask-A.yaml").read_text())


def test_examples_load_and_pair():
    a = load_manifest(EXAMPLES / "flask-A.yaml")
    b = load_manifest(EXAMPLES / "flask-B.yaml")
    assert (a.condition, b.condition) == ("A", "B")
    res = check_paired(a, b)
    assert res.ok, res.failures


def test_unknown_condition_fails(tmp_path, base):
    base["condition"] = "C"
    with pytest.raises(ManifestError, match="condition"):
        load_manifest(_write(tmp_path, base))


def test_missing_model_id_fails(tmp_path, base):
    del base["models"]["generator"]
    with pytest.raises(ManifestError, match="generator"):
        load_manifest(_write(tmp_path, base))


def test_condition_b_requires_wiki_snapshot(tmp_path, base):
    base["condition"] = "B"
    with pytest.raises(ManifestError, match="wiki_snapshot_id"):
        load_manifest(_write(tmp_path, base))


@pytest.mark.parametrize(
    "path,value",
    [
        (("models", "reranker"), "other-model"),
        (("models", "generator"), "other-model"),
        (("budgets", "context_tokens"), 4000),
        (("budgets", "rerank_top_n"), 8),
        (("web", "mode"), "live"),
        (("web", "enabled"), False),
    ],
)
def test_check_paired_fails_on_shared_stage_difference(tmp_path, path, value):
    a = load_manifest(EXAMPLES / "flask-A.yaml")
    bd = yaml.safe_load((EXAMPLES / "flask-B.yaml").read_text())
    bd[path[0]][path[1]] = value
    b = load_manifest(_write(tmp_path, bd))
    res = check_paired(a, b)
    assert not res.ok
    assert any(path[0] in f for f in res.failures)


def test_check_paired_reports_but_passes_repo_budget_difference(tmp_path):
    a = load_manifest(EXAMPLES / "flask-A.yaml")
    bd = yaml.safe_load((EXAMPLES / "flask-B.yaml").read_text())
    bd["budgets"]["wiki_max_spans"] = 15
    b = load_manifest(_write(tmp_path, bd))
    res = check_paired(a, b)
    assert res.ok, res.failures
    assert any("repository candidate budget" in n for n in res.notes)


def test_same_condition_is_not_a_pair():
    a = load_manifest(EXAMPLES / "flask-A.yaml")
    assert not check_paired(a, a).ok


def test_prompt_hash_changes_with_text(tmp_path):
    p = tmp_path / "p.md"
    p.write_text("v1")
    h1 = PromptRef(path="p.md").resolve(tmp_path).sha256
    p.write_text("v2")
    h2 = PromptRef(path="p.md").resolve(tmp_path).sha256
    assert h1 != h2 and h1 == prompt_hash("v1")
    with pytest.raises(ValueError, match="hash mismatch"):
        PromptRef(path="p.md", sha256=h1).resolve(tmp_path)


# --- conditional web search --------------------------------------------------------------


def test_conditional_web_search_defaults_on(tmp_path, base):
    base["web"].pop("conditional_web_search", None)
    m = load_manifest(_write(tmp_path, base))
    assert m.web.conditional_web_search is True


@pytest.mark.parametrize("enabled,conditional,has_prompt,ok", [
    (True, True, False, False),   # the gate needs its judge prompt
    (True, True, True, True),
    (True, False, False, True),   # flag off: no judge
    (False, True, False, True),   # web off: no judge
])
def test_judge_prompt_is_required_only_to_run_the_gate(tmp_path, base, enabled, conditional,
                                                        has_prompt, ok):
    base["web"]["enabled"] = enabled
    base["web"]["conditional_web_search"] = conditional
    if not has_prompt:
        base["prompts"].pop("sufficiency", None)
    m = load_manifest(_write(tmp_path, base))  # always loads (saved runs must stay readable)
    if ok:
        require_runnable(m)
    else:
        with pytest.raises(ManifestError, match="prompts.sufficiency"):
            require_runnable(m)


def test_saved_run_from_before_the_gate_still_loads_and_pairs(tmp_path, base):
    """A pre-change run manifest (web on, no flag, no judge prompt) stays loadable, so
    `experiment evaluate` keeps accepting it."""
    base["prompts"].pop("sufficiency", None)
    base["web"].pop("conditional_web_search", None)
    a = load_manifest(_write(tmp_path, base, "a.yaml"))
    bd = dict(base, condition="B", indexes={**base["indexes"], "wiki_snapshot_id": "w"})
    b = load_manifest(_write(tmp_path, bd, "b.yaml"))
    assert check_paired(a, b).ok
    assert not check_paired(a, b, runnable=True).ok


@pytest.mark.parametrize("path,value", [
    (("web", "conditional_web_search"), False),
    (("prompts", "sufficiency"), {"path": "src/dwb/prompt_files/generate_v2.md"}),
])
def test_check_paired_fails_when_the_gate_differs(tmp_path, path, value):
    a = load_manifest(EXAMPLES / "flask-A.yaml")
    bd = yaml.safe_load((EXAMPLES / "flask-B.yaml").read_text())
    bd[path[0]][path[1]] = value
    b = load_manifest(_write(tmp_path, bd))
    res = check_paired(a, b)
    assert not res.ok
    assert any(path[0] in f for f in res.failures)


@pytest.mark.parametrize("repo", ["flask", "requests"])
@pytest.mark.parametrize("suffix", ["", "-dev"])
def test_shipped_pairs_load_and_pass_the_paired_check(repo, suffix):
    a = load_manifest(EXAMPLES / f"{repo}-A{suffix}.yaml")
    b = load_manifest(EXAMPLES / f"{repo}-B{suffix}.yaml")
    assert check_paired(a, b).ok
    split = "dev" if suffix else "test"
    assert a.question_set.path == f"data/questions/{split}/{repo}.jsonl"
    # the test manifests carry the frozen hash; dev sets are not frozen
    frozen = (ROOT / f"data/questions/test/{repo}.jsonl.sha256").read_text().strip()
    assert a.question_set.hash == (None if suffix else frozen)


def test_requests_runs_without_a_pdf_index():
    m = load_manifest(EXAMPLES / "requests-A.yaml")
    assert m.pdf_enabled is False and m.indexes.pdf_index_id is None
