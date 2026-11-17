from dwb.benchmark.schemas import AnnotatedSpan
from dwb.eval.matching import line_jaccard, matches


def code_ev(path, s, e):
    return {"source_type": "code",
            "locator": {"kind": "code", "commit": "x", "path": path, "start_line": s,
                        "end_line": e}}


def span(path="src/a.py", s=10, e=20):
    return AnnotatedSpan(source_type="code", path=path, start_line=s, end_line=e)


def test_one_line_overlap_matches_adjacent_does_not():
    assert matches(code_ev("src/a.py", 20, 30), span())
    assert not matches(code_ev("src/a.py", 21, 30), span())
    assert not matches(code_ev("src/b.py", 10, 20), span())


def test_jaccard_variant():
    assert line_jaccard((10, 20), (10, 20)) == 1.0
    assert matches(code_ev("src/a.py", 12, 20), span(), jaccard=True)  # 9/11
    assert not matches(code_ev("src/a.py", 20, 40), span(), jaccard=True)  # 1/31
    assert matches(code_ev("src/a.py", 20, 40), span())  # but the overlap rule matches


def test_pdf_and_web():
    pdf = {"source_type": "pdf", "locator": {"doc_id": "pep3333", "page": 4}}
    assert matches(pdf, AnnotatedSpan(source_type="pdf", doc_id="pep3333", page=4))
    assert not matches(pdf, AnnotatedSpan(source_type="pdf", doc_id="pep3333", page=5))
    web = {"source_type": "web", "text": "The 3xx class indicates further action is needed.",
           "locator": {"url": "https://www.rfc-editor.org/rfc/rfc9110#section-15.4"}}
    assert matches(web, AnnotatedSpan(source_type="web", url="https://www.rfc-editor.org/rfc/rfc9110"))
    assert matches(web, AnnotatedSpan(source_type="web", url="https://www.rfc-editor.org/rfc/rfc9110",
                                      passage="3xx class indicates further action"))
    assert not matches(web, AnnotatedSpan(source_type="web", url="https://www.rfc-editor.org/rfc/rfc9110",
                                          passage="cookies expire at session end"))
