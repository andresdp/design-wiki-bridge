"""`dwb benchmark` curation: drafting, validation, freezing, and checking question sets."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from dwb.workspace import Workspace, WorkspaceError

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "tests" / "fixtures" / "toy_repo"
REV = "rev-1"
SHA = "a" * 40


def _split_rows(n=4):
    return "".join(json.dumps({"question": f"q{i}", "answer": f"a{i}"}) + "\n" for i in range(n))


def make_ws(tmp_path, *, split_text=None, pin_sha=None, pdf_corpus=True) -> Workspace:
    """A research workspace with one repository ('toy') and a pinned 'toy' SWE-QA split."""
    split_text = _split_rows() if split_text is None else split_text
    (tmp_path / "artifacts" / "swe-qa").mkdir(parents=True)
    (tmp_path / "artifacts" / "swe-qa" / "toy.jsonl").write_text(split_text)
    digest = pin_sha or hashlib.sha256(split_text.encode()).hexdigest()
    (tmp_path / "configs" / "pdfs").mkdir(parents=True)
    (tmp_path / "configs" / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {"toy": {"url": "https://example.org/toy.git", "sha": SHA,
                          "checkout": "artifacts/checkouts/toy", "swe_qa_split": "toy"}},
        "swe_qa": {"dataset": "swe-qa/SWE-QA-Benchmark", "revision": REV,
                   "local_dir": "artifacts/swe-qa", "sha256": {"toy.jsonl": digest}}}))
    chat = {"repos": {"toy": {"pdf_corpus": "configs/pdfs/toy.yaml"}}} if pdf_corpus else {}
    (tmp_path / "configs" / "chat.yaml").write_text(yaml.safe_dump(chat))
    return Workspace(root=tmp_path)


def selection(tmp_path, **kw) -> Path:
    d = dict(split="toy", dataset_revision=REV, repo="toy", repo_sha=SHA, id_prefix="toy-pub",
             include=[{"row": 1, "reason": "cross-file"}, {"row": 3}],
             exclude=[{"row": 2, "reason": "answer drifts from pinned SHA"}])
    d.update(kw)
    p = tmp_path / "configs" / "selection.yaml"
    p.write_text(yaml.safe_dump(d))
    return p


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


# ----------------------------------------------------------------------------- U1: draft


def test_swe_qa_pin_and_split_name_come_from_repos_yaml(tmp_path):
    ws = make_ws(tmp_path)
    pin = ws.swe_qa_pin()
    assert pin.revision == REV and pin.local_dir == Path("artifacts/swe-qa")
    assert "toy.jsonl" in pin.sha256 and ws.repo("toy").swe_qa_split == "toy"


def test_draft_writes_records_and_exclusions(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    res = draft(ws, selection(tmp_path))
    recs, excl = _lines(res.records_path), _lines(res.exclusions_path)
    assert res.records_path == tmp_path / "data/questions/drafts/toy.public.jsonl"
    assert [r["provenance"]["upstream_row_id"] for r in recs] == ["toy:1", "toy:3"]
    assert recs[0]["id"] == "toy-pub-001" and recs[0]["reference_answer"] == "a1"
    assert excl == [{"row_id": "toy:2", "reason": "answer drifts from pinned SHA"}]
    assert (res.n_records, res.n_excluded) == (2, 1)


def test_draft_never_overwrites_without_flag(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    res = draft(ws, selection(tmp_path))
    res.records_path.write_text("annotated\n")
    with pytest.raises(WorkspaceError, match="--overwrite"):
        draft(ws, selection(tmp_path))
    assert res.records_path.read_text() == "annotated\n"
    draft(ws, selection(tmp_path), overwrite=True)
    assert len(_lines(res.records_path)) == 2


@pytest.mark.parametrize("field,value,needle", [
    ("dataset_revision", "other-rev", REV),
    ("repo_sha", "b" * 40, SHA),
    ("split", "flask", "swe_qa_split"),
    ("file_sha256", "0" * 64, "sha256"),
])
def test_draft_refuses_selections_that_disagree_with_the_pins(tmp_path, field, value, needle):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError, match=needle):
        draft(ws, selection(tmp_path, **{field: value}))


def test_missing_split_is_fetched_at_the_pinned_revision(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    text = (tmp_path / "artifacts/swe-qa/toy.jsonl").read_text()
    (tmp_path / "artifacts/swe-qa/toy.jsonl").unlink()
    calls = []

    def fake_fetch(split_file, revision, local_dir):
        calls.append((split_file, revision))
        p = Path(local_dir) / split_file
        p.write_text(text)
        return p

    res = draft(ws, selection(tmp_path), fetch=fake_fetch)
    assert calls == [("toy.jsonl", REV)] and res.n_records == 2


def test_split_hash_mismatch_names_the_file(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path, pin_sha="0" * 64)
    with pytest.raises(WorkspaceError, match="toy.jsonl"):
        draft(ws, selection(tmp_path))


def test_unknown_selection_row_is_a_setup_error(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError, match="not in split"):
        draft(ws, selection(tmp_path, include=[{"row": 40}], exclude=[]))
