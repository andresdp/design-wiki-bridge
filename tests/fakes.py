"""Offline fake chat models that implement structured output (shared by tests)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import PrivateAttr


class FakeStructuredChat(BaseChatModel):
    """Answers every call with JSON from ``responder(messages) -> dict``.

    ``with_structured_output(schema)`` parses that JSON into ``schema``. The underlying
    call goes through the normal chat-model path, so callbacks receive usage metadata.
    """

    input_tokens: int = 100
    output_tokens: int = 10
    _responder: Callable[[list[BaseMessage]], dict] = PrivateAttr()
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, responder: Callable[[list[BaseMessage]], dict] | dict, **kw: Any):
        super().__init__(**kw)
        self._responder = responder if callable(responder) else (lambda _m: responder)

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    @property
    def _llm_type(self) -> str:
        return "fake-structured"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._calls.append(messages)
        payload = self._responder(messages)
        msg = AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                            "total_tokens": self.input_tokens + self.output_tokens},
            response_metadata={"model_name": "fake-structured"},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(self, schema, **kwargs):
        def parse(msg: AIMessage):
            data = json.loads(msg.content)
            if isinstance(schema, type):
                return schema.model_validate(data)
            return data

        return self | RunnableLambda(parse)
