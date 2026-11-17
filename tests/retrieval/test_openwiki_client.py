import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dwb.retrieval.openwiki_client import (
    OpenWikiClient,
    OpenWikiError,
    WikiSnapshot,
    parse_read,
    parse_search,
    register_snapshot,
    restore_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
FX = ROOT / "tests" / "fixtures"
FLASK = ROOT / "artifacts" / "checkouts" / "flask-3.1.2"


def fixture(name):
    return json.loads((FX / "mcp_responses" / name).read_text())


def test_parse_search_fixture_ranked_refs():
    refs = parse_search(fixture("q1_wsgi_dispatch_search.json"))
    assert len(refs) == 10 and [r.rank for r in refs] == list(range(1, 11))
    assert refs[0].page == "openwiki/architecture/request-runtime.md"
    assert refs[0].anchor == "dispatch-and-routing"
    assert refs[0].title.startswith("WSGI Runtime")


def test_parse_read_fixture_section_and_claims():
    refs = parse_search(fixture("q1_wsgi_dispatch_search.json"))[:1]
    [sec] = parse_read(fixture("q1_wsgi_dispatch_read.json"), refs, FX / "openwiki")
    assert sec.text.startswith("## Dispatch and routing")
    assert sec.ref.ref == "openwiki/architecture/request-runtime.md#dispatch-and-routing"
    claims = sec.raw["page_claims"]
    assert len(claims) == 10 and all(c["statement"] for c in claims)
    assert all(r.startswith("repo://") for r in sec.claim_evidence)
    assert sec.raw["section_index"] is not None
    assert all(s.startswith("repo://") for s in sec.sources)


def test_empty_search_is_empty_list():
    assert parse_search(fixture("empty_search.json")) == []


def test_tool_error_raises_typed_error():
    with pytest.raises(OpenWikiError, match="inside a Git repository"):
        parse_search({"isError": True, "content": [
            {"type": "text", "text": "invalid_input: The OpenWiki root must be inside a Git "
                                     "repository."}]})


def test_server_start_failure_includes_stderr(tmp_path):
    fake = tmp_path / "openwiki"
    fake.write_text("#!/bin/sh\necho 'boom: node too old' >&2\nexit 3\n")
    fake.chmod(0o755)

    async def go():
        async with OpenWikiClient(tmp_path, command=str(fake)):
            pass

    with pytest.raises(OpenWikiError) as e:
        asyncio.run(go())
    assert "boom: node too old" in str(e.value)


def _git_repo(path: Path) -> str:
    path.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q")
    (path / "a.py").write_text("x = 1\n")
    run("add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


def test_snapshot_register_restore_and_id_changes(tmp_path):
    repo = tmp_path / "repo"
    sha = _git_repo(repo)
    shutil.copytree(FX / "openwiki" / "openwiki", repo / "openwiki")
    last = json.loads((repo / "openwiki" / ".last-update.json").read_text())
    last["gitHead"] = sha
    (repo / "openwiki" / ".last-update.json").write_text(json.dumps(last))
    kw = dict(provider="openai", model="gpt-5.6-luna", version="0.9.0", effort="medium",
              elapsed_s=1054.92, cost_usd=None)
    snap = register_snapshot(repo, "toy", sha, tmp_path / "wikis", **kw)
    assert snap.pages == 1 and snap.claims == 10 and snap.sha == sha
    for field in ("version", "provider", "model", "sha", "elapsed_s", "cost_usd"):
        assert field in snap.model_dump()
    other = WikiSnapshot(**{**snap.model_dump(), "cost_usd": 12.5})
    assert other.compute_id() != snap.snapshot_id
    assert WikiSnapshot(**{**snap.model_dump(), "model": "x"}).compute_id() != snap.snapshot_id
    shutil.rmtree(repo / "openwiki")
    restored = restore_snapshot(tmp_path / "wikis" / snap.snapshot_id, repo)
    assert restored.snapshot_id == snap.snapshot_id and (repo / "openwiki").is_dir()
    with pytest.raises(OpenWikiError, match="generated with"):
        register_snapshot(repo, "toy", sha, tmp_path / "w2", **{**kw, "model": "other"})


@pytest.mark.live
@pytest.mark.skipif(not (FLASK / "openwiki").exists() or not shutil.which("openwiki"),
                    reason="needs the Flask wiki and openwiki 0.9.0")
def test_live_search_and_read_flask():
    async def go():
        async with OpenWikiClient(FLASK) as wiki:
            refs = await wiki.search("How does Flask dispatch a request to a view?", 5)
            secs = await wiki.read(refs[:2])
            return wiki, refs, secs

    wiki, refs, secs = asyncio.run(go())
    assert refs and secs and wiki.startup_stage.latency_ms > 0
    assert os.environ.get("OPENWIKI_TELEMETRY_DISABLED", "1")


def test_hung_tool_call_times_out():
    class Hung:
        async def call_tool(self, tool, args):
            await asyncio.sleep(10)

    c = OpenWikiClient(Path("."), command="/bin/true", call_timeout_s=0.05)
    c._session = Hung()
    with pytest.raises(OpenWikiError, match="did not answer within"):
        asyncio.run(c.search("q", 3))


def test_server_log_removed_after_a_clean_session():
    c = OpenWikiClient(Path("."), command="/bin/true")
    log = c._errlog
    assert log.exists()
    asyncio.run(c.__aexit__(None, None, None))
    assert not log.exists()
