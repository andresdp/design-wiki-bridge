"""The web gate's judge: is the local evidence enough to answer the question?

Used by the ``check_local`` node when ``web.conditional_web_search`` is on. One judge, model,
prompt and evidence budget serve both conditions, so the rule is identical by construction
even when the verdicts differ. The judge never answers the question.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from dwb.budget import Context
from dwb.fatal import raise_if_fatal
from dwb.prompts import load_prompt
from dwb.trace import StageRecord
from dwb.usage import llm_usage


class _JudgeOutput(BaseModel):
    """Whether the evidence lets every part of the question be answered."""

    sufficient: bool = Field(description="True only if every part of the question is "
                                         "supported by at least one passage.")
    reason: str = Field(description="One sentence: what is covered, or what is missing.")


@dataclass
class Verdict:
    sufficient: bool | None  # None: the judge call failed
    reason: str = ""


class SufficiencyJudge:
    def __init__(self, llm: BaseChatModel, prompt_path: str | Path, *,
                 model_id: str | None = None, structured_method: str | None = None):
        self.prompt, self.prompt_hash = load_prompt(prompt_path)
        kw = {"method": structured_method} if structured_method else {}
        self.chain = self.prompt | llm.with_structured_output(_JudgeOutput, **kw)
        self.model_id = model_id or getattr(llm, "model_name", None) or type(llm).__name__
        self.structured_method = structured_method

    async def ajudge(self, question: str, context: Context) -> tuple[Verdict, StageRecord]:
        usage = UsageMetadataCallbackHandler()
        t0 = time.perf_counter()
        out: _JudgeOutput | None = None
        error = None
        try:
            out = await self.chain.ainvoke({"question": question, "context": context.text},
                                           config={"callbacks": [usage]})
            if not isinstance(out, _JudgeOutput):
                out = _JudgeOutput.model_validate(out)
        except Exception as e:
            raise_if_fatal(e)
            error = f"{type(e).__name__}: {e}"
            out = None
        u = llm_usage(usage, [HumanMessage(question), HumanMessage(context.text)],
                      out.model_dump_json() if out else None)
        verdict = Verdict(out.sufficient, out.reason) if out else Verdict(None, "")
        stage = StageRecord(
            name="check_local",
            latency_ms=(time.perf_counter() - t0) * 1000,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            candidate_ids=list(context.ids),
            count=len(context.ids),
            extra={"model": self.model_id, "prompt_hash": self.prompt_hash, **u.extra(),
                   "structured_method": self.structured_method,
                   "sufficient": verdict.sufficient, "error": error},
        )
        return verdict, stage
