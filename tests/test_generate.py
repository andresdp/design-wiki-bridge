import asyncio
from pathlib import Path

from dwb.budget import assemble_context
from dwb.evidence import code_item
from dwb.generate import GeneratedAnswer, Generator, render_answer, validate_claims
from fakes import FakeStructuredChat

PROMPT = Path(__file__).resolve().parents[1] / "src" / "dwb" / "prompt_files" / "generate_v1.md"


def ctx():
    return assemble_context(
        [code_item("s", "a.py", 1, 3, "def a(): ..."), code_item("s", "b.py", 5, 9, "class B")],
        10_000,
    )


def gen(payload):
    return asyncio.run(Generator(FakeStructuredChat(payload), PROMPT).agenerate("q?", ctx()))


def test_canned_context_validates_with_zero_unresolved():
    c = ctx()
    answer, stage = gen({"claims": [{"text": "a is defined", "evidence_ids": ["E1"]},
                                    {"text": "B is a class", "evidence_ids": ["E2", "E1"]}],
                         "summary": "Both exist."})
    claims, report = validate_claims(answer, c)
    assert report["n_unresolved_ids"] == 0
    assert claims[1].evidence_ids == [c.aliases["E2"], c.aliases["E1"]]
    assert stage.input_tokens == 100 and stage.extra["prompt_hash"]


def test_unknown_alias_logged_and_claim_kept():
    c = ctx()
    claims, report = validate_claims(
        GeneratedAnswer(claims=[{"text": "x", "evidence_ids": ["E99", "E1"]}]), c)
    assert claims[0].unresolved_aliases == ["E99"]
    assert claims[0].evidence_ids == [c.aliases["E1"]]
    assert report["unresolved"] == [{"claim_index": 0, "aliases": ["E99"]}]


def test_claim_without_citations_kept_and_marked_uncited():
    claims, report = validate_claims(GeneratedAnswer(claims=[{"text": "no cite"}]), ctx())
    assert claims[0].uncited and report["uncited_claims"] == [0]


def test_parse_failure_is_recorded_not_raised():
    answer, stage = gen({"not_claims": 1})
    assert answer is None
    assert "ValidationError" in stage.extra["error"]


def test_rendered_answer_has_every_claim_in_order():
    c = ctx()
    claims, _ = validate_claims(GeneratedAnswer(claims=[
        {"text": "first", "evidence_ids": ["E2"]}, {"text": "second", "evidence_ids": []}]), c)
    text = render_answer(claims, "Sum.", c.aliases)
    assert text.index("first") < text.index("second")
    assert "first [E2]" in text and text.startswith("Sum.")


def test_prompt_receives_question_and_context():
    fake = FakeStructuredChat({"claims": []})
    asyncio.run(Generator(fake, PROMPT).agenerate("Where is a?", ctx()))
    human = fake.calls[0][-1].content
    assert "Where is a?" in human and "[E1] code a.py" in human
