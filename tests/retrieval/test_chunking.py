import logging
from pathlib import Path

from dwb.repo import RepoReader
from dwb.retrieval.chunking import ChunkingConfig, chunk_repo, file_spans, python_spans
from dwb.tokens import count_tokens

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "toy_repo"
CFG = ChunkingConfig()

SRC = '''def a():
    return 1


def b():
    return 2


class C:
    def m(self):
        return 3
'''


def test_two_functions_and_class_with_method_yield_four_chunks():
    spans = python_spans("x.py", SRC, CFG)
    got = [(s.kind, s.symbol, s.start, s.end) for s in spans]
    assert got == [
        ("function", "a", 1, 2),
        ("function", "b", 5, 6),
        ("class", "C", 9, 9),
        ("method", "C.m", 10, 11),
    ]


def test_decorators_belong_to_their_definition():
    src = "import x\n\n@x.deco\ndef f():\n    pass\n"
    spans = python_spans("x.py", src, CFG)
    f = next(s for s in spans if s.symbol == "f")
    assert (f.start, f.end) == (3, 5)
    assert any(s.kind == "window" and s.start == 1 for s in spans)  # module-level import


def test_non_python_and_module_code_use_overlapping_windows():
    text = "\n".join(f"line {i}" for i in range(1, 131))
    spans = file_spans("notes.md", text, CFG)
    assert [(s.start, s.end) for s in spans] == [(1, 60), (51, 110), (101, 130)]
    assert all(s.kind == "window" for s in spans)


def test_syntax_error_falls_back_to_windows(caplog):
    with caplog.at_level(logging.WARNING):
        spans = python_spans("bad.py", "def (:\n  pass\n", CFG)
    assert [s.kind for s in spans] == ["window"]
    assert "syntax error" in caplog.text


def test_large_class_split_under_cap_and_covers_range():
    body = "\n".join(f"    attr_{i} = {i} * 1000 + {i}  # padding text here" for i in range(400))
    src = f"class Big:\n{body}\n"
    cfg = ChunkingConfig(max_span_tokens=300)
    spans = file_spans("big.py", src, cfg)
    assert len(spans) > 1 and all(s.symbol == "Big" for s in spans)
    assert spans[0].start == 1 and spans[-1].end == 401
    for prev, nxt in zip(spans, spans[1:], strict=False):
        assert nxt.start == prev.end + 1
    lines = src.splitlines()
    assert all(count_tokens("\n".join(lines[s.start - 1 : s.end])) <= 300 for s in spans)


def test_chunk_toy_repo_items_have_valid_locators():
    items = chunk_repo(RepoReader.from_dir(TOY, sha="toysha"), CFG)
    run = [i for i in items if i.provenance.get("symbol") == "App.run"]
    assert len(run) == 1
    loc = run[0].locator
    assert (loc.path, loc.start_line, loc.end_line, loc.commit) == ("src/toy/app.py", 23, 25,
                                                                    "toysha")
    assert run[0].text.lstrip().startswith("def run")
    assert any(i.locator.path == "README.md" for i in items)


def test_class_body_after_the_first_method_is_indexed():
    src = '''class Session:
    """doc"""

    def first(self):
        return 1

    new = False
    modified = False

    def second(self):
        return 2

    accessed = False
'''
    spans = python_spans("s.py", src, CFG)
    got = [(s.kind, s.start, s.end) for s in spans]
    assert ("class", 1, 3) in got
    assert ("class_body", 6, 9) in got  # between the methods
    assert ("class_body", 12, 13) in got  # after the last method
    covered = {i for s in spans for i in range(s.start, s.end + 1)}
    for line_no, line in enumerate(src.splitlines(), start=1):
        if line.strip():
            assert line_no in covered, line


def test_class_without_trailing_body_has_no_empty_spans():
    spans = python_spans("x.py", SRC, CFG)
    assert not [s for s in spans if s.kind == "class_body"]
