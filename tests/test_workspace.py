"""Research-mode artifact discovery picks the newest matching index or snapshot."""

import json
from pathlib import Path

from dwb.workspace import Workspace

ROOT_DIR = Path(__file__).resolve().parents[1]


def _index(root, idx, kind, created, **parts):
    d = root / "artifacts/indexes" / idx
    d.mkdir(parents=True)
    (d / "index.json").write_text(json.dumps({"index_id": idx, "kind": kind,
                                              "embedding_model": parts.pop("emb", "e1"),
                                              "n_items": 1, "parts": parts,
                                              "created_at": created}))


def test_newest_matching_index_wins_and_mismatches_are_ignored(tmp_path):
    _index(tmp_path, "code-old", "code", "2026-01-01", repo="r", sha="s")
    _index(tmp_path, "code-new", "code", "2026-06-01", repo="r", sha="s")
    _index(tmp_path, "code-other-model", "code", "2027-01-01", repo="r", sha="s", emb="e2")
    _index(tmp_path, "code-other-sha", "code", "2027-01-01", repo="r", sha="t")
    _index(tmp_path, "pdf-a", "pdf", "2026-01-01", corpus_id="c")
    ws = Workspace(root=tmp_path)
    assert ws.find_code_index("r", "s", "e1") == "code-new"
    assert ws.find_code_index("r", "s", "e2") == "code-other-model"
    assert ws.find_code_index("r", "x", "e1") is None
    assert ws.find_pdf_index("c", "e1") == "pdf-a" and ws.find_pdf_index("c", "e2") is None


def test_newest_wiki_snapshot_for_the_sha(tmp_path):
    for sid, at, sha in (("w1", "2026-01-01", "s"), ("w2", "2026-02-01", "s"),
                         ("w3", "2027-01-01", "t")):
        d = tmp_path / "artifacts/wikis" / sid
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text(json.dumps({"snapshot_id": sid, "repo": "r", "sha": sha,
                                                     "registered_at": at}))
    ws = Workspace(root=tmp_path)
    assert ws.find_wiki_snapshot("r", "s")["snapshot_id"] == "w2"
    assert ws.find_wiki_snapshot("r", "nope") is None
    assert ws.history_path == tmp_path / "artifacts/chat_history"


def test_chat_defaults_load_the_shipped_config():
    from dwb.workspace import ChatDefaults

    d = ChatDefaults.load(ROOT_DIR / "configs" / "chat.yaml")
    assert d.models.embedding and d.sources.pdf and not d.sources.wiki
    assert d.repo("flask").pdf_corpus == "configs/pdfs/flask.yaml"
    assert "github.com" in d.repo("flask").web_exclude_domains
    assert d.repo("unknown").web_exclude_domains == [] and d.budgets.context_tokens == 8000


def test_chat_defaults_missing_file_and_bad_keys(tmp_path):
    import pytest

    from dwb.workspace import ChatDefaults, WorkspaceError

    assert ChatDefaults.load(tmp_path / "none.yaml") == ChatDefaults()
    bad = tmp_path / "chat.yaml"
    bad.write_text("sources:\n  pdfs: true\nweb:\n  search_depth: deep\n")
    with pytest.raises(WorkspaceError) as e:
        ChatDefaults.load(bad)
    assert "sources.pdfs" in str(e.value) and "web.search_depth" in str(e.value)

