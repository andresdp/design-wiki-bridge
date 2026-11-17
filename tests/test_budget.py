from dwb.budget import assemble_context, format_item
from dwb.evidence import code_item
from dwb.tokens import count_tokens


def items():
    return [
        code_item("s", "a.py", 1, 2, "short a"),
        code_item("s", "b.py", 1, 2, "word " * 400),
        code_item("s", "c.py", 1, 2, "short c"),
    ]


def test_budget_excludes_whole_items_and_keeps_a_prefix():
    its = items()
    first = count_tokens(format_item("E1", its[0])) + 2
    ctx = assemble_context(its, budget_tokens=first + 50)
    assert ctx.ids == [its[0].id]  # c.py would fit, but the set stays a prefix
    assert ctx.excluded_ids == [its[1].id, its[2].id]
    assert "short a" in ctx.text and "word" not in ctx.text
    assert ctx.tokens <= first + 50


def test_aliases_contiguous_and_one_to_one():
    its = items()
    ctx = assemble_context(its, budget_tokens=10_000)
    assert list(ctx.aliases) == ["E1", "E2", "E3"]
    assert list(ctx.aliases.values()) == [i.id for i in its]
    assert len(set(ctx.aliases.values())) == 3
    assert "[E2] code b.py lines 1-2" in ctx.text


def test_empty_input():
    ctx = assemble_context([], 100)
    assert ctx.empty and ctx.aliases == {} and ctx.text == ""
