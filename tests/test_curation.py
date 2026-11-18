"""`dwb benchmark` curation: drafting, validation, freezing, and checking question sets."""

import hashlib
import json
import re
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


def test_split_download_failure_is_a_setup_error(tmp_path):
    from dwb.curation import draft

    ws = make_ws(tmp_path)
    (tmp_path / "artifacts/swe-qa/toy.jsonl").unlink()

    def offline(split_file, revision, local_dir):
        raise OSError("offline")

    with pytest.raises(WorkspaceError, match=f"toy.jsonl at SWE-QA revision {REV}: offline"):
        draft(ws, selection(tmp_path), fetch=offline)


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


# ----------------------------------------------------------------------- U3: freeze, check


def run_freeze(ws, path, **kw):
    from dwb.curation import freeze_set

    return freeze_set(ws, path, reader_for=toy_reader, **kw)


def write_dev(tmp_path, *rows) -> Path:
    p = tmp_path / "data" / "questions" / "dev" / "toy.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_freeze_writes_the_hash_the_runner_accepts(tmp_path):
    from dwb.benchmark.freeze import records_hash
    from dwb.benchmark.schemas import load_records
    from dwb.runner import load_question_set
    from test_graph import manifest

    ws = make_ws(tmp_path)
    with_corpus(tmp_path)
    p = write_records(tmp_path, pub(), cus())
    res = run_freeze(ws, p)
    assert res.ok and res.digest == records_hash(load_records(p))
    assert Path(f"{p}.sha256").read_text().strip() == res.digest
    m = manifest("A")
    m = m.model_copy(update={"repo_sha": SHA, "question_set": m.question_set.model_copy(
        update={"path": str(p.relative_to(tmp_path)), "hash": res.digest})})
    assert len(load_question_set(m, tmp_path)) == 2


def test_freeze_refuses_a_set_with_problems(tmp_path):
    ws = make_ws(tmp_path)
    p = write_records(tmp_path, pub(), pub())
    res = run_freeze(ws, p)
    assert not res.ok and res.digest is None and "duplicate id" in res.report.problems[0]
    assert not Path(f"{p}.sha256").exists()


def test_freeze_refuses_overlap_with_the_dev_set(tmp_path):
    ws = make_ws(tmp_path)
    write_dev(tmp_path, pub(id="toy-dev-pub-001", question="dev wording?"))
    p = write_records(tmp_path, pub())
    res = run_freeze(ws, p)
    assert not res.ok and any("toy:1" in x for x in res.report.problems)
    assert not Path(f"{p}.sha256").exists()


def test_freeze_reports_a_malformed_dev_file_and_writes_nothing(tmp_path):
    ws = make_ws(tmp_path)
    dev = write_dev(tmp_path)
    dev.write_text("{not json\n")
    p = write_records(tmp_path, pub())
    with pytest.raises(WorkspaceError, match=re.escape(f"invalid {dev}")):
        run_freeze(ws, p)
    assert not Path(f"{p}.sha256").exists()


def test_check_passes_on_disjoint_sets_and_fails_on_overlap(tmp_path):
    from dwb.curation import check_sets

    dev = write_dev(tmp_path, pub(id="toy-dev-pub-001", question="dev?",
                                  provenance={"upstream_split": "toy",
                                              "upstream_row_id": "toy:0"}))
    test = write_records(tmp_path, pub())
    assert check_sets(dev, test).ok
    test = write_records(tmp_path, pub(id="toy-pub-000", provenance={
        "upstream_split": "toy", "upstream_row_id": "toy:0"}))
    res = check_sets(dev, test)
    assert not res.ok and "toy:0" in res.problems[0]


def test_check_reports_a_stale_freeze(tmp_path):
    from dwb.benchmark.freeze import freeze
    from dwb.curation import check_sets

    dev = write_dev(tmp_path, pub(id="toy-dev-pub-009", question="dev?", provenance={
        "upstream_split": "toy", "upstream_row_id": "toy:9"}))
    test = write_records(tmp_path, pub())
    freeze(test)
    assert check_sets(dev, test).ok
    write_records(tmp_path, pub(question="edited after freezing?"))
    res = check_sets(dev, test)
    assert not res.ok and "sha256" in res.problems[0]
    Path(f"{test}.sha256").unlink()
    assert check_sets(dev, test).ok


def test_check_treats_a_missing_test_file_as_empty(tmp_path):
    from dwb.curation import check_sets

    dev = write_dev(tmp_path, pub())
    res = check_sets(dev, tmp_path / "data/questions/test/none.jsonl")
    assert res.ok and "does not exist" in res.warnings[0]


def test_check_needs_the_dev_file(tmp_path):
    from dwb.curation import check_sets

    with pytest.raises(WorkspaceError, match="not found"):
        check_sets(tmp_path / "nope.jsonl", tmp_path / "also-nope.jsonl")


@pytest.mark.parametrize("bad", ["dev", "test"])
def test_check_reports_a_malformed_file_by_name(tmp_path, bad):
    from dwb.curation import check_sets

    dev = write_dev(tmp_path, pub(id="toy-dev-pub-009", question="dev?", provenance={
        "upstream_split": "toy", "upstream_row_id": "toy:9"}))
    test = write_records(tmp_path, pub())
    target = dev if bad == "dev" else test
    target.write_text(target.read_text() + '{"id": "broken"}\n')
    with pytest.raises(WorkspaceError, match=re.escape(f"invalid {target}")):
        check_sets(dev, test)


# ------------------------------------------------------------------------------- U4: CLI


@pytest.fixture
def cli_ws(tmp_path, monkeypatch):
    import dwb.cli as cli
    import dwb.curation as curation

    make_ws(tmp_path)
    with_corpus(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "WS", Workspace())
    monkeypatch.setattr(curation, "git_reader", toy_reader)
    return tmp_path


def invoke(*args):
    from typer.testing import CliRunner

    import dwb.cli as cli

    return CliRunner().invoke(cli.app, ["benchmark", *args])


def test_cli_validate_exit_codes(cli_ws):
    p = write_records(cli_ws, pub(), pub())
    r = invoke("validate", str(p))
    assert r.exit_code == 1 and "duplicate id" in r.output
    p = write_records(cli_ws, pub(), cus())
    r = invoke("validate", str(p))
    assert r.exit_code == 0, r.output
    assert '"public": 1' in r.output and '"custom": 1' in r.output


def test_cli_draft_pin_mismatch_is_a_usage_error(cli_ws):
    r = invoke("draft", str(selection(cli_ws, repo_sha="b" * 40)))
    assert r.exit_code == 2 and SHA in r.output
    r = invoke("draft", str(selection(cli_ws)))
    assert r.exit_code == 0 and "toy.public.jsonl" in r.output


def test_cli_freeze_prints_the_hash_and_hint(cli_ws):
    p = write_records(cli_ws, pub(), cus())
    r = invoke("freeze", str(p))
    assert r.exit_code == 0, r.output
    digest = Path(f"{p}.sha256").read_text().strip()
    assert digest in r.output and "question_set.hash" in r.output


def test_cli_check_names_the_overlap(cli_ws):
    dev = write_dev(cli_ws, pub(id="toy-dev-pub-001", question="dev?"))
    r = invoke("check", str(dev), str(write_records(cli_ws, pub())))
    assert r.exit_code == 1 and "toy:1" in r.output
    r = invoke("check", str(dev), str(cli_ws / "missing.jsonl"))
    assert r.exit_code == 0 and "does not exist" in r.output


@pytest.mark.parametrize("cmd", [[], ["draft", "--help"], ["validate", "--help"],
                                 ["freeze", "--help"], ["check", "--help"]])
def test_benchmark_help(cmd):
    r = invoke(*cmd)
    assert r.exit_code in (0, 2) and "Usage" in r.output


def _dumps(x) -> str:
    return json.dumps(x, indent=2) + "\n"


def test_cli_piped_json_is_unchanged(cli_ws):
    """Each benchmark command prints exactly the JSON it printed before (indent=2, same keys)."""
    sel = selection(cli_ws)
    r = invoke("draft", str(sel))
    d = Path("data/questions/drafts")
    assert r.stdout == _dumps({"records": str(d / "toy.public.jsonl"), "n_records": 2,
                               "exclusions": str(d / "toy.excluded.jsonl"), "n_excluded": 1})
    p = write_records(cli_ws, pub(), cus())
    r = invoke("validate", str(p))
    assert r.stdout == _dumps({"path": str(p), "ok": True, "counts": json.loads(r.stdout)[
        "counts"], "problems": []})
    r = invoke("freeze", str(p))
    digest = Path(f"{p}.sha256").read_text().strip()
    assert r.stdout == _dumps({"path": str(p), "hash": digest,
                               "counts": json.loads(r.stdout)["counts"]})
    dev = write_dev(cli_ws, pub(id="toy-dev-pub-001", question="dev?"))
    r = invoke("check", str(dev), str(p))
    out = json.loads(r.stdout)
    assert r.exit_code == 1 and r.stdout == _dumps(
        {"dev": str(dev), "test": str(p), "ok": False, "problems": out["problems"],
         "warnings": []})


def test_cli_at_a_terminal_prints_header_summary_and_outcome(cli_ws, tty):
    r = invoke("draft", str(selection(cli_ws)))
    assert r.exit_code == 0, r.output
    assert r.stderr.startswith(f"toy · research mode · {SHA[:12]}\n")
    assert re.search(r"^records\s+2 in .*toy\.public\.jsonl$", r.stdout, re.M)
    assert "✓ Drafted 2 records; annotate them, then validate → next: dwb benchmark validate " \
           in r.stderr
    p = write_records(cli_ws, pub(), cus())
    r = invoke("validate", str(p))
    assert r.exit_code == 0 and r.stderr.startswith("toy.jsonl · research mode\n")
    assert re.search(r"^public\s+1$", r.stdout, re.M) and "{" not in r.stdout
    assert f"✓ {p} is valid → next: dwb benchmark freeze {p}" in r.stderr
    bad = write_records(cli_ws, pub(), pub())
    r = invoke("validate", str(bad))
    assert r.exit_code == 1 and "duplicate id" in r.stderr and "✗ 1 problems in" in r.stderr
    p = write_records(cli_ws, pub(), cus())
    r = invoke("freeze", str(p))
    digest = Path(f"{p}.sha256").read_text().strip()
    assert r.exit_code == 0 and re.search(rf"^hash\s+{re.escape(digest)}$", r.stdout, re.M)
    assert f"✓ Frozen {p}. Set question_set.hash: {digest}" in r.stderr
    dev = write_dev(cli_ws, pub(id="toy-dev-pub-009", question="dev?", provenance={
        "upstream_split": "toy", "upstream_row_id": "toy:9"}))
    r = invoke("check", str(dev), str(p))
    assert r.exit_code == 0 and "✓" in r.stderr and "disjoint" in r.stderr
    from typer.testing import CliRunner

    import dwb.cli as cli

    r = CliRunner().invoke(cli.app, ["--json", "benchmark", "check", str(dev), str(p)])
    assert json.loads(r.stdout)["ok"] is True and "research mode" not in r.stderr
