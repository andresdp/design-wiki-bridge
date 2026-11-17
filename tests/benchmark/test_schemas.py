from pathlib import Path

import pytest
from pydantic import ValidationError

from dwb.benchmark.schemas import (
    QuestionRecord,
    load_records,
    save_records,
    validate_against_checkout,
)
from dwb.repo import RepoReader

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "toy_repo"


def public(**kw):
    base = dict(
        id="flask-pub-000", kind="public", repo="toy", repo_sha="fixture",
        question="q?", reference_answer="a",
        provenance={"upstream_split": "flask", "upstream_row_id": "flask:0"},
    )
    base.update(kw)
    return QuestionRecord(**base)


def custom(**kw):
    base = dict(
        id="toy-cus-000", kind="custom", repo="toy", repo_sha="fixture",
        question="How does the WSGI entry build the app?",
        allowed_sources=["code", "pdf"],
        expected_claims=[{"id": "c1", "text": "application() calls make_app()"}],
        evidence=[
            {"id": "e1", "supports_claims": ["c1"], "alternatives": [
                {"source_type": "code", "path": "src/toy/wsgi.py", "start_line": 6,
                 "end_line": 9}]},
            {"id": "e2", "alternatives": [
                {"source_type": "pdf", "doc_id": "pep3333", "page": 3}]},
        ],
        provenance={"author": "annotator-1"},
    )
    base.update(kw)
    return QuestionRecord(**base)


def test_public_with_web_fails():
    with pytest.raises(ValidationError, match="R8"):
        public(allowed_sources=["code", "web"])


def test_public_needs_upstream_ids():
    with pytest.raises(ValidationError, match="upstream"):
        public(provenance={})


def test_custom_valid():
    assert custom().kind == "custom"


def test_custom_without_claims_fails():
    with pytest.raises(ValidationError, match="expected_claims"):
        custom(expected_claims=[], evidence=[])


def test_custom_without_external_span_fails():
    with pytest.raises(ValidationError, match="external"):
        custom(evidence=[{"id": "e1", "alternatives": [
            {"source_type": "code", "path": "a.py", "start_line": 1, "end_line": 2}]}])


def test_unknown_supported_claim_fails():
    rec = custom().model_dump()
    rec["evidence"][0]["supports_claims"] = ["c9"]
    with pytest.raises(ValidationError, match="c9"):
        QuestionRecord(**rec)


def test_span_past_end_of_file_fails_against_toy_repo():
    reader = RepoReader.from_dir(TOY)
    assert validate_against_checkout(custom(), reader) == []
    bad = custom().model_dump()
    bad["evidence"][0]["alternatives"][0]["end_line"] = 400
    problems = validate_against_checkout(QuestionRecord(**bad), reader)
    assert len(problems) == 1 and "past end of file" in problems[0]
    bad["evidence"][0]["alternatives"][0]["path"] = "src/toy/missing.py"
    assert "does not exist" in validate_against_checkout(QuestionRecord(**bad), reader)[0]


def test_round_trip(tmp_path):
    recs = [public(), custom()]
    save_records(recs, tmp_path / "x.jsonl")
    assert load_records(tmp_path / "x.jsonl") == recs
