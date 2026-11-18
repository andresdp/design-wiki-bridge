from pathlib import Path

from conftest import make_trace
from dwb.eval.claims_judge import ClaimsJudge
from fakes import FakeStructuredChat

PROMPTS = Path(__file__).resolve().parents[2] / "src" / "dwb" / "prompt_files"


def judge_fn(messages):
    human = messages[-1].content
    if "Cited passages" in human:
        # supported only when both passages are present (joint support)
        return {"supported": "[i1]" in human and "[i2]" in human, "rationale": ""}
    return {"verdict": "supported" if "A calls B" in human.split("Answer claims")[0]
            else "partial", "rationale": ""}


def make_judge(tmp_path=None):
    return ClaimsJudge(FakeStructuredChat(judge_fn), PROMPTS, model_id="fake-judge",
                       cache_path=(tmp_path / "cache.jsonl") if tmp_path else None)


def test_joint_support_and_coverage(record):
    sup = make_judge().support(make_trace())
    # claim 1 cites only i1 -> unsupported; claim 2 cites i1+i2 -> jointly supported
    assert sup["citation_support"] == 0.5
    assert sup["coverage"] == 1 / 3  # uncited claim counts as uncovered
    assert sup["n_cited"] == 2


def test_correctness_with_partial_credit(record):
    res = make_judge().correctness(record, make_trace())
    assert res["verdicts"] == {"c1": "supported", "c2": "partial"}
    assert res["score"] == 0.75


def test_no_answer_scores_missing_without_calls(record):
    j = make_judge()
    res = j.correctness(record, make_trace(claims=[]))
    assert res["score"] == 0.0 and j.stats.calls == 0


def test_second_evaluation_uses_cache(tmp_path, record):
    j1 = make_judge(tmp_path)
    j1.correctness(record, make_trace())
    j1.support(make_trace())
    assert j1.stats.calls == 4
    j2 = make_judge(tmp_path)
    j2.correctness(record, make_trace())
    j2.support(make_trace())
    assert j2.stats.calls == 0 and j2.stats.cache_hits == 4


def test_gap_claims_are_not_judged_or_counted_in_coverage():
    from dwb.trace import AnswerClaim

    t = make_trace(claims=[
        AnswerClaim(text="B returns C", cited_aliases=["E1", "E2"], evidence_ids=["i1", "i2"]),
        AnswerClaim(text="The default is not shown.", kind="gap")])
    j = make_judge()
    sup = j.support(t)
    assert sup["coverage"] == 1.0 and sup["n_gap"] == 1 and sup["n_claims"] == 1
    assert j.stats.calls == 1


def test_cache_is_keyed_by_judge_model(tmp_path, record):
    j1 = make_judge(tmp_path)
    j1.correctness(record, make_trace())
    assert j1.stats.calls > 0
    same = make_judge(tmp_path)
    same.correctness(record, make_trace())
    assert same.stats.calls == 0 and same.stats.cache_hits > 0
    other = ClaimsJudge(FakeStructuredChat(judge_fn), PROMPTS, model_id="another-judge",
                        cache_path=tmp_path / "cache.jsonl")
    other.correctness(record, make_trace())
    assert other.stats.cache_hits == 0 and other.stats.calls == j1.stats.calls
    hotter = ClaimsJudge(FakeStructuredChat(judge_fn), PROMPTS, model_id="fake-judge",
                         temperature=0.7, cache_path=tmp_path / "cache.jsonl")
    hotter.correctness(record, make_trace())
    assert hotter.stats.cache_hits == 0
    assert j1.stats.log[0]["model"] == "fake-judge"
