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


# ------------------------------------------------------------------------- U2: validate


def toy_reader(repo):
    from dwb.repo import RepoReader

    return RepoReader.from_dir(TOY, sha=repo.sha)


def make_pdf(path: Path, pages: int) -> None:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def with_corpus(tmp_path, pages=2, docs=("toydoc",)):
    (tmp_path / "configs" / "pdfs" / "toy.yaml").write_text(yaml.safe_dump({
        "corpus_id": "toy-v1", "documents": [
            {"doc_id": d, "version": f"{d}-1", "path": f"artifacts/pdfs/{d}.pdf"} for d in docs]}))
    for d in docs:
        make_pdf(tmp_path / "artifacts" / "pdfs" / f"{d}.pdf", pages)


def pub(**kw):
    d = dict(id="toy-pub-001", kind="public", repo="toy", repo_sha=SHA, question="q1?",
             reference_answer="a1",
             provenance={"upstream_split": "toy", "upstream_row_id": "toy:1"})
    d.update(kw)
    return d


def cus(code=("src/toy/wsgi.py", 6, 9), pdf=("toydoc", 1), **kw):
    d = dict(id="toy-cus-001", kind="custom", repo="toy", repo_sha=SHA,
             question="How does the WSGI entry build the app?",
             allowed_sources=["code", "pdf"],
             expected_claims=[{"id": "c1", "text": "application() calls make_app()"}],
             evidence=[
                 {"id": "e1", "supports_claims": ["c1"], "alternatives": [
                     {"source_type": "code", "path": code[0], "start_line": code[1],
                      "end_line": code[2]}]},
                 {"id": "e2", "alternatives": [
                     {"source_type": "pdf", "doc_id": pdf[0], "page": pdf[1]}]}],
             provenance={"author": "annotator-1"})
    d.update(kw)
    return d


def write_records(tmp_path, *rows) -> Path:
    p = tmp_path / "data" / "questions" / "test" / "toy.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return p


def run_validate(ws, path, **kw):
    from dwb.curation import validate

    return validate(ws, path, reader_for=kw.pop("reader_for", toy_reader), **kw)


def test_clean_file_has_no_problems_and_counts(tmp_path):
    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    rep = run_validate(ws, write_records(tmp_path, pub(), cus()))
    assert rep.ok and rep.problems == []
    assert rep.counts == {"records": 2, "public": 1, "custom": 1, "without_evidence": 1,
                          "expected_claims": 1}


def test_bad_lines_are_reported_and_the_rest_still_checked(tmp_path):
    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    p = write_records(tmp_path, "{not json", pub(allowed_sources=["code", "web"]),
                      cus(code=("src/toy/wsgi.py", 6, 400)))
    probs = run_validate(ws, p).problems
    assert any(x.startswith("line 1:") for x in probs)
    assert any(x.startswith("line 2:") and "web" in x for x in probs)
    assert any("past end of file" in x for x in probs) and len(probs) == 3


def test_duplicate_ids_reported_once(tmp_path):
    ws = make_ws(tmp_path)
    probs = run_validate(ws, write_records(tmp_path, pub(), pub(question="other?"))).problems
    assert probs == ["toy-pub-001: duplicate id"]


def test_code_spans_past_end_or_on_missing_files(tmp_path):
    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    p = write_records(tmp_path, cus(code=("src/toy/wsgi.py", 6, 400)),
                      cus(id="toy-cus-002", code=("src/toy/missing.py", 1, 2)))
    probs = run_validate(ws, p).problems
    assert len(probs) == 2 and "past end of file" in probs[0] and "does not exist" in probs[1]


def test_pdf_spans_unknown_doc_or_page_past_end(tmp_path):
    ws = make_ws(tmp_path)
    with_corpus(tmp_path, pages=2)
    p = write_records(tmp_path, cus(pdf=("nodoc", 1)), cus(id="toy-cus-002", pdf=("toydoc", 3)))
    probs = run_validate(ws, p).problems
    assert len(probs) == 2
    assert "nodoc" in probs[0] and "not in the PDF corpus" in probs[0]
    assert "page 3" in probs[1] and "2 pages" in probs[1]


def test_missing_pdf_file_reported_once(tmp_path):
    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    (tmp_path / "artifacts" / "pdfs" / "toydoc.pdf").unlink()
    p = write_records(tmp_path, cus(), cus(id="toy-cus-002"))
    probs = run_validate(ws, p).problems
    assert len(probs) == 1 and "toydoc.pdf" in probs[0]


def test_sha_mismatch_reported_once(tmp_path):
    from dwb.repo import RepoReader

    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    git_like = RepoReader(SHA, root=TOY)
    git_like.git_dir = TOY  # as validate_against_checkout sees a git-backed reader
    p = write_records(tmp_path, cus(repo_sha="c" * 40))
    probs = run_validate(ws, p, reader_for=lambda repo: git_like).problems
    assert len(probs) == 1 and "repo_sha" in probs[0]


def test_unknown_repo_and_pdf_without_corpus_are_problems(tmp_path):
    ws = make_ws(tmp_path, pdf_corpus=False)
    p = write_records(tmp_path, pub(repo="nope"), cus())
    probs = run_validate(ws, p).problems
    assert any("nope" in x and "configs/repos.yaml" in x for x in probs)
    assert any("pdf_corpus" in x for x in probs)


def test_pdf_corpus_override(tmp_path):
    ws = make_ws(tmp_path, pdf_corpus=False)
    with_corpus(tmp_path)
    p = write_records(tmp_path, cus())
    assert run_validate(ws, p, pdf_corpus=Path("configs/pdfs/toy.yaml")).ok


def test_missing_checkout_is_a_setup_error(tmp_path):
    from dwb.curation import validate

    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    with pytest.raises(WorkspaceError, match="Adding a research repository"):
        validate(ws, write_records(tmp_path, cus()))


def test_public_items_without_spans_need_no_checkout(tmp_path):
    from dwb.curation import validate

    ws = make_ws(tmp_path)
    assert validate(ws, write_records(tmp_path, pub())).ok
