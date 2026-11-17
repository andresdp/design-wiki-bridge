from pathlib import Path

import pytest
import yaml

from dwb.manifest import (
    ManifestError,
    PromptRef,
    check_paired,
    load_manifest,
    prompt_hash,
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
