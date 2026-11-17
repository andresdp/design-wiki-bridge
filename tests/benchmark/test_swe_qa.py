import json
from pathlib import Path

import pytest
import yaml

from dwb.benchmark.schemas import validate_against_checkout
from dwb.benchmark.swe_qa import Selection, build_drafts, file_sha256, load_split
from dwb.repo import RepoReader

ROOT = Path(__file__).resolve().parents[2]


def _split(tmp_path, n=4):
    p = tmp_path / "flask.jsonl"
    p.write_text("".join(json.dumps({"question": f"q{i}", "answer": f"a{i}"}) + "\n"
                         for i in range(n)))
    return p


def _selection(**kw):
    d = dict(split="flask", dataset_revision="rev", repo="flask", repo_sha="sha",
             id_prefix="flask-pub", include=[{"row": 1, "reason": "cross-file"}, {"row": 3}],
             exclude=[{"row": 2, "reason": "answer drifts from pinned SHA"}])
    d.update(kw)
    return Selection.model_validate(d)


def test_loader_keeps_upstream_ids_and_exclusions(tmp_path):
    rows = load_split(_split(tmp_path), "flask")
    recs, excl = build_drafts(_selection(), rows)
    assert [r.provenance.upstream_row_id for r in recs] == ["flask:1", "flask:3"]
    assert all(r.provenance.upstream_split == "flask" for r in recs)
    assert all(r.allowed_sources == ["code"] for r in recs)
    assert recs[0].id == "flask-pub-001" and recs[0].reference_answer == "a1"
    assert [(e.row_id, e.reason) for e in excl] == [("flask:2", "answer drifts from pinned SHA")]


def test_pinned_hash_checked(tmp_path):
    p = _split(tmp_path)
    assert load_split(p, "flask", file_sha256(p))
    with pytest.raises(ValueError, match="sha256"):
        load_split(p, "flask", "0" * 64)


def test_row_in_both_lists_rejected(tmp_path):
    rows = load_split(_split(tmp_path), "flask")
    with pytest.raises(ValueError, match="both"):
        build_drafts(_selection(exclude=[{"row": 1}]), rows)


def test_unknown_row_rejected(tmp_path):
    rows = load_split(_split(tmp_path), "flask")
    with pytest.raises(ValueError, match="not in split"):
        build_drafts(_selection(include=[{"row": 40}], exclude=[]), rows)


def test_pinned_flask_split_against_checkout():
    """Draft records from the pinned SWE-QA file validate against the pinned checkout."""
    cfg = yaml.safe_load((ROOT / "configs" / "repos.yaml").read_text())
    raw = ROOT / cfg["swe_qa"]["local_dir"] / "flask.jsonl"
    repo = cfg["repos"]["flask"]
    checkout = ROOT / repo["checkout"]
    if not raw.exists() or not checkout.exists():
        pytest.skip("pinned SWE-QA files or Flask checkout not present (see U1 spike doc)")
    rows = load_split(raw, "flask", cfg["swe_qa"]["sha256"]["flask.jsonl"])
    assert len(rows) == 48
    sel = _selection(dataset_revision=cfg["swe_qa"]["revision"], repo_sha=repo["sha"],
                     include=[{"row": i} for i in (0, 1, 2, 4, 5)], exclude=[])
    recs, _ = build_drafts(sel, rows)
    reader = RepoReader.from_git(checkout, repo["sha"])
    assert all(validate_against_checkout(r, reader) == [] for r in recs)
