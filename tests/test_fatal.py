"""Out of credit or a bad key stops a run (no failed traces), and --resume finishes it."""

import json
import shutil
from pathlib import Path

import httpx
import openai
import pytest
from tavily.errors import UsageLimitExceededError

from dwb.benchmark.freeze import freeze
from dwb.benchmark.schemas import save_records
from dwb.fatal import FatalAPIError, is_fatal, raise_if_fatal
from dwb.generate import Generator
from dwb.manifest import QuestionSetRef
from dwb.rerank import LLMListwiseReranker
from dwb.runner import RunStopped, capture_web, run_manifest
from dwb.trace import ResumeError, read_traces
from dwb.web.tavily_store import TavilyStore
from fakes import FakeStructuredChat
from graph_fakes import PROMPT, cite_all, components
from test_graph import custom_q, manifest, public_q

ROOT = Path(__file__).resolve().parents[1]


def quota_error():
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/x"))
    return openai.RateLimitError("You exceeded your current quota", response=resp,
                                 body={"code": "insufficient_quota"})


def rate_limit():
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/x"))
    return openai.RateLimitError("Rate limit reached", response=resp,
                                 body={"code": "rate_limit_exceeded"})


def auth_error():
    resp = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1/x"))
    return openai.AuthenticationError("Incorrect API key", response=resp, body=None)


def test_fatal_classification():
    assert is_fatal(quota_error()) and is_fatal(auth_error())
    assert is_fatal(UsageLimitExceededError("plan limit"))
    assert not is_fatal(rate_limit())  # retried by the client, not fatal
    assert not is_fatal(ValueError("x"))
    try:  # a wrapper keeps the provider error in its cause chain
        try:
            raise quota_error()
        except openai.RateLimitError as e:
            raise RuntimeError("chain failed") from e
    except RuntimeError as wrapped:
        assert is_fatal(wrapped)
        with pytest.raises(FatalAPIError):
            raise_if_fatal(wrapped)
    raise_if_fatal(ValueError("ordinary errors pass through"))


def setup(tmp_path, records):
    shutil.copytree(ROOT / "src/dwb/prompt_files", tmp_path / "src/dwb/prompt_files")
    rel = "data/questions/test/flask.jsonl"
    save_records(records, tmp_path / rel)
    m = manifest("A")
    return m.model_copy(update={"question_set": QuestionSetRef(
        id="t", path=rel, hash=freeze(tmp_path / rel))})


def questions(n=3):
    return [public_q().model_copy(update={"id": f"flask-pub-00{i}"}) for i in range(n)]


def failing_generator(after: int, err=quota_error):
    """A generator that answers ``after`` questions, then raises ``err()``."""
    calls = []

    def respond(messages):
        calls.append(1)
        if len(calls) > after:
            raise err()
        return cite_all(messages)

    return Generator(FakeStructuredChat(respond), PROMPT, model_id="fake")


def run(m, c, tmp_path, **kw):
    return run_manifest(m, c, base_dir=tmp_path, runs_dir=tmp_path / "runs", **kw)


def test_out_of_credit_stops_the_run_and_resume_finishes_it(tmp_path):
    m = setup(tmp_path, questions(3))
    c = components("A")
    c.generator = failing_generator(after=1)
    with pytest.raises(RunStopped) as stop:
        run(m, c, tmp_path)
    rd = tmp_path / "runs" / m.run_id
    traces = read_traces(rd / "traces.jsonl")
    assert [t.question_id for t in traces] == ["flask-pub-000"]  # nothing saved as failed
    summary = json.loads((rd / "run_summary.json").read_text())
    assert summary["stopped"] and summary["completed"] == 1 and summary["failed"] == 0
    assert stop.value.summary["completed"] == 1

    with pytest.raises(FileExistsError):  # a plain rerun does not silently restart
        run(m, components("A"), tmp_path)
    done = run(m, components("A"), tmp_path, resume=True)
    traces = read_traces(rd / "traces.jsonl")
    assert [t.question_id for t in traces] == [f"flask-pub-00{i}" for i in range(3)]
    assert done["completed"] == 3 and done["failed"] == 0 and "stopped" not in done
    assert done["resumes"][0]["kept"] == 1


def test_ordinary_rate_limit_still_fails_one_question(tmp_path):
    m = setup(tmp_path, questions(2))
    c = components("A")
    c.generator = failing_generator(after=0, err=rate_limit)
    s = run(m, c, tmp_path)
    assert s["completed"] == 2 and "stopped" not in s  # recorded per question, not stopped
    assert s["failed"] == 2


def test_fatal_reranker_error_is_not_a_pool_order_fallback(tmp_path):
    m = setup(tmp_path, questions(1))
    c = components("A")

    def boom(_m):
        raise quota_error()

    c.reranker = LLMListwiseReranker(FakeStructuredChat(boom), pool_cap=40, top_n=12,
                                     model_id="fake")
    with pytest.raises(RunStopped):
        run(m, c, tmp_path)
    assert read_traces(tmp_path / "runs" / m.run_id / "traces.jsonl") == []


def test_resume_keeps_failed_questions_and_checks_the_manifest(tmp_path):
    """A failed question scores 0 and is rerun only by hand in every condition (protocol §6),
    so --resume runs only the questions without a trace."""
    m = setup(tmp_path, questions(3))
    calls = []

    def respond(messages):  # q0 ok, q1 an ordinary failure, q2 out of credit
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("bad json")
        if len(calls) == 3:
            raise quota_error()
        return cite_all(messages)

    c = components("A")
    c.generator = Generator(FakeStructuredChat(respond), PROMPT, model_id="fake")
    with pytest.raises(RunStopped):
        run(m, c, tmp_path)
    s = run(m, components("A"), tmp_path, resume=True)
    assert s["completed"] == 3 and s["failed"] == 1  # q1 stays failed
    traces = read_traces(tmp_path / "runs" / m.run_id / "traces.jsonl")
    assert [t.question_id for t in traces] == ["flask-pub-000", "flask-pub-001", "flask-pub-002"]
    assert traces[1].errors

    changed = m.model_copy(update={"budgets": m.budgets.model_copy(update={"code_top_k": 7})})
    with pytest.raises(ResumeError, match="differs"):
        run(changed, components("A"), tmp_path, resume=True)
    with pytest.raises(ValueError, match="exclude"):
        run(m, components("A"), tmp_path, resume=True, overwrite=True)


def test_resume_needs_an_existing_run(tmp_path):
    m = setup(tmp_path, questions(1))
    with pytest.raises(ResumeError, match="no run"):
        run(m, components("A"), tmp_path, resume=True)


def test_web_only_capture_stores_what_a_replay_run_asks_for(tmp_path):
    from test_tavily_store import StubClient

    eligible = custom_q()
    m = manifest("A")
    stub = StubClient()
    store = TavilyStore(tmp_path / "tav", m.web.store_id, mode="record", client=stub)
    s = capture_web(m, [public_q(), eligible], store)
    assert s["eligible"] == 1 and s["captured_live"] == 1 and s["web_capture_failed"] == 0
    again = capture_web(m, [public_q(), eligible], store)
    assert again["already_stored"] == 1 and again["captured_live"] == 0
    assert len(stub.calls) == 1
    # a replay graph run of a gated condition finds the stored request
    from test_graph import run as graph_run

    replay = TavilyStore(tmp_path / "tav", m.web.store_id, mode="replay")
    t = graph_run(m, components("A", web_store=replay), eligible)
    assert t.web_triggered and not t.errors and not t.warnings


def test_web_only_capture_stops_on_an_exhausted_plan(tmp_path):
    class Exhausted:
        calls = []

        def search(self, *a, **k):
            raise UsageLimitExceededError("plan limit")

    store = TavilyStore(tmp_path, "s", mode="record", client=Exhausted())
    with pytest.raises(FatalAPIError):
        capture_web(manifest("A"), [custom_q()], store)

