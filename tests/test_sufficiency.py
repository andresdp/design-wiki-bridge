import asyncio

from dwb.budget import assemble_context
from dwb.evidence import code_item
from dwb.prompts import SUFFICIENCY_PROMPT, USER_MARKER, load_prompt
from dwb.sufficiency import SufficiencyJudge
from fakes import FakeStructuredChat


def ctx():
    return assemble_context([code_item("s", "a.py", 1, 3, "def a(): ...")], 10_000)


def judge(llm, **kw):
    return asyncio.run(SufficiencyJudge(llm, SUFFICIENCY_PROMPT, model_id="fake")
                       .ajudge("How is a defined?", ctx(), **kw))


def test_sufficient_verdict_with_reason_and_usage():
    llm = FakeStructuredChat({"sufficient": True, "reason": "a.py defines a."})
    verdict, stage = judge(llm)
    assert verdict.sufficient is True and verdict.reason == "a.py defines a."
    assert stage.name == "check_local"
    assert stage.input_tokens == 100 and stage.output_tokens == 10
    assert stage.extra["prompt_hash"] and stage.extra["model"] == "fake"
    assert stage.extra["error"] is None
    # the judge reads the question and the evidence text, and is not asked to answer
    human = llm.calls[0][-1].content
    assert "How is a defined?" in human and "def a()" in human


def test_insufficient_verdict():
    verdict, _ = judge(FakeStructuredChat({"sufficient": False, "reason": "No caller shown."}))
    assert verdict.sufficient is False


def test_a_failing_model_gives_no_verdict_and_does_not_raise():
    def boom(_messages):
        raise RuntimeError("rate limited")

    verdict, stage = judge(FakeStructuredChat(boom))
    assert verdict.sufficient is None
    assert "RuntimeError: rate limited" in stage.extra["error"]


def test_prompt_file_takes_question_and_context():
    assert USER_MARKER in SUFFICIENCY_PROMPT.read_text()
    template, digest = load_prompt(SUFFICIENCY_PROMPT)
    assert set(template.input_variables) == {"question", "context"} and len(digest) == 64
