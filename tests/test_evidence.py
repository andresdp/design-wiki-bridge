import pytest
from pydantic import ValidationError

from dwb.evidence import (
    CodeLocator,
    EvidenceItem,
    PdfLocator,
    WebLocator,
    code_item,
    evidence_id,
)

SHA = "abc123"


def test_same_locator_same_id_and_range_change_changes_id():
    a = code_item(SHA, "src/app.py", 10, 20, "x")
    b = code_item(SHA, "src/app.py", 10, 20, "different text, same span")
    c = code_item(SHA, "src/app.py", 10, 21, "x")
    assert a.id == b.id
    assert a.id != c.id
    assert a.id.startswith("ev_")


def test_source_type_is_part_of_the_id():
    loc = CodeLocator(commit=SHA, path="a.py", start_line=1, end_line=2)
    assert evidence_id("code", loc) != evidence_id("pdf", loc)


@pytest.mark.parametrize(
    "item",
    [
        code_item(SHA, "src/app.py", 3, 9, "def f(): ...", branch="vector", rank=1, score=0.5),
        EvidenceItem(
            source_type="pdf",
            text="page text",
            locator=PdfLocator(doc_id="pep3333", doc_version="2010-09-26", page=4),
        ),
        EvidenceItem(
            source_type="web",
            text="passage",
            locator=WebLocator(url="https://example.org/a", capture_id="cap1", passage_index=0),
            provenance={"branch": "web"},
        ),
    ],
)
def test_document_round_trip(item):
    doc = item.to_document()
    assert doc.metadata["evidence_id"] == item.id
    back = EvidenceItem.from_document(doc)
    assert back == item


@pytest.mark.parametrize("start,end", [(5, 4), (0, 3), (-1, 2)])
def test_bad_code_ranges_rejected(start, end):
    with pytest.raises(ValidationError):
        CodeLocator(commit=SHA, path="a.py", start_line=start, end_line=end)


def test_absolute_code_path_rejected():
    with pytest.raises(ValidationError):
        CodeLocator(commit=SHA, path="/etc/a.py", start_line=1, end_line=1)


def test_pdf_without_version_rejected():
    with pytest.raises(ValidationError):
        PdfLocator(doc_id="rfc9110", page=1)
    with pytest.raises(ValidationError):
        PdfLocator(doc_id="rfc9110", doc_version="", page=1)


def test_mismatched_source_type_rejected():
    with pytest.raises(ValidationError):
        EvidenceItem(
            source_type="pdf",
            text="x",
            locator=CodeLocator(commit=SHA, path="a.py", start_line=1, end_line=1),
        )


def test_tampered_id_rejected():
    item = code_item(SHA, "a.py", 1, 2, "x")
    with pytest.raises(ValidationError):
        EvidenceItem(id="ev_0000000000000000", source_type="code", text="x", locator=item.locator)


def test_sort_is_stable_across_source_types():
    items = [
        EvidenceItem(
            source_type="web",
            text="w",
            locator=WebLocator(url="https://a", capture_id="c", passage_index=1),
        ),
        code_item(SHA, "b.py", 10, 12, "b"),
        EvidenceItem(
            source_type="pdf", text="p", locator=PdfLocator(doc_id="d", doc_version="1", page=2)
        ),
        code_item(SHA, "b.py", 2, 3, "b2"),
        code_item(SHA, "a.py", 100, 120, "a"),
    ]
    once = sorted(items, key=lambda i: i.sort_key())
    twice = sorted(reversed(items), key=lambda i: i.sort_key())
    assert [i.id for i in once] == [i.id for i in twice]
    assert [i.source_type for i in once] == ["code", "code", "code", "pdf", "web"]
    # numeric (not lexicographic) line ordering
    assert [i.locator.start_line for i in once[:3]] == [100, 2, 10]
    assert once[1].locator.path == "b.py" and once[1].locator.start_line == 2
