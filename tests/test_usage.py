from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage

from dwb.usage import approx_tokens, embedding_usage, llm_usage
from fakes import FakeStructuredChat
from test_graph import manifest, public_q, run


def test_provider_usage_from_langchain_callback():
    handler = UsageMetadataCallbackHandler()
    FakeStructuredChat({"x": 1}, input_tokens=50, output_tokens=7).invoke(
        "hi", config={"callbacks": [handler]})
    u = llm_usage(handler, [HumanMessage("ignored")], "ignored")
    assert (u.input_tokens, u.output_tokens, u.source) == (50, 7, "provider")
    assert u.models == ("fake-structured",)


def test_approximate_fallback_when_no_usage_reported():
    u = llm_usage(UsageMetadataCallbackHandler(), [HumanMessage("x" * 400)], "y" * 40)
    assert u.source == "approximate" and u.input_tokens >= 100 and u.output_tokens >= 10
    assert llm_usage(UsageMetadataCallbackHandler()).source == "none"


def test_embedding_usage_is_approximate_and_names_the_model():
    u = embedding_usage("How does Flask dispatch a request?", "emb-x")
    assert u.source == "approximate" and u.input_tokens > 0 and u.models == ("emb-x",)
    assert approx_tokens([]) == 0


def test_retrieval_and_llm_stages_record_tokens():
    from graph_fakes import FakeRetriever, code_items, components

    class Retr(FakeRetriever):
        embedding_model = "emb-x"

    c = components("A", pdf=False)
    c.code_retriever = Retr(code_items())
    t = run(manifest("A"), c, public_q())
    st = {s.name: s for s in t.stages}
    assert st["code_retrieve"].input_tokens > 0
    assert st["code_retrieve"].extra["embedding_model"] == "emb-x"
    assert st["code_retrieve"].extra["tokens_source"] == "approximate"
    for name in ("rerank", "generate"):
        assert st[name].extra["tokens_source"] == "provider" and st[name].input_tokens == 100
