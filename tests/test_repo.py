"""RepoReader guards and git-compatible line splitting."""

from pathlib import Path

from dwb.repo import RepoReader, split_lines

TOY = Path(__file__).resolve().parent / "fixtures" / "toy_repo"


def test_reads_inside_the_root_only(tmp_path):
    r = RepoReader.from_dir(TOY)
    assert r.read("src/toy/app.py") is not None
    assert r.read("./src/toy/app.py") == r.read("src/toy/app.py")
    assert (TOY / "../../test_repo.py").is_file()
    assert r.read("../../test_repo.py") is None  # an existing file outside the root
    assert r.read("/etc/hosts") is None
    assert r.read("src/toy") is None  # a directory is not a file
    assert r.read("src/toy/missing.py") is None


def test_span_bounds():
    r = RepoReader.from_dir(TOY)
    n = r.line_count("src/toy/app.py")
    assert n and n > 2
    assert r.span_text("src/toy/app.py", 1, n) is not None
    assert r.span_text("src/toy/app.py", 0, 1) is None
    assert r.span_text("src/toy/app.py", 1, n + 1) is None
    assert r.span_text("src/toy/app.py", 3, 2) is None
    assert r.span_text("src/toy/nope.py", 1, 1) is None


def test_split_lines_counts_like_git():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("a\r\nb\r\n") == ["a", "b"]
    assert split_lines("") == []
    # form feed and the Unicode separators are not line breaks for git or ast
    text = "x = 1\n\x0c\ny = ' '\nz = 3\n"
    assert len(split_lines(text)) == 4 and split_lines(text)[3] == "z = 3"
    assert len(text.splitlines()) == 6  # what str.splitlines would have done
