from dwb.evidence import code_item
from dwb.merge import merge_candidates


def test_identical_locator_merged_and_alias_recorded():
    a = code_item("s", "a.py", 1, 10, "x", branch="vector", rank=1)
    b = code_item("s", "a.py", 1, 10, "x", branch="wiki", rank=4)
    out, alias = merge_candidates([a, b])
    assert [i.id for i in out] == [a.id]
    assert out[0].provenance["also_from"] == ["wiki"]
    assert alias == {}  # same ID: nothing to alias


def test_contained_span_dropped_larger_survives_with_alias():
    inner = code_item("s", "a.py", 5, 8, "inner")
    outer = code_item("s", "a.py", 1, 20, "outer")
    other = code_item("s", "b.py", 5, 8, "other file")
    out, alias = merge_candidates([inner, other, outer])
    assert [i.id for i in out] == [outer.id, other.id]  # outer takes inner's position
    assert alias[inner.id] == outer.id
    assert inner.id in out[0].provenance["merged_ids"]


def test_different_commit_not_merged():
    a = code_item("s1", "a.py", 1, 20, "x")
    b = code_item("s2", "a.py", 5, 8, "y")
    out, alias = merge_candidates([a, b])
    assert len(out) == 2 and alias == {}


def test_chain_of_merges_keeps_every_alias_resolvable():
    small = code_item("s", "a.py", 5, 6, "s")
    mid = code_item("s", "a.py", 4, 8, "m")
    big = code_item("s", "a.py", 1, 30, "b")
    out, alias = merge_candidates([small, mid, big])
    assert [i.id for i in out] == [big.id]
    assert alias[small.id] == big.id and alias[mid.id] == big.id
    assert set(out[0].provenance["merged_ids"]) == {small.id, mid.id}


def test_inputs_not_mutated():
    inner = code_item("s", "a.py", 5, 8, "inner")
    outer = code_item("s", "a.py", 1, 20, "outer")
    merge_candidates([outer, inner])
    assert "merged_ids" not in outer.provenance
