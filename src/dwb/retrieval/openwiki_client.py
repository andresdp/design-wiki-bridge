"""OpenWiki snapshots (KTD6, R11) and the async MCP client (KTD2, U7).

The MCP server (``openwiki mcp``) is rootless: every call passes ``root``, which must be
an absolute git checkout top level containing ``openwiki/``. A snapshot is a copy of the
generated ``openwiki/`` tree plus ``snapshot.json``. ``restore_snapshot`` puts it back
into a checkout at the pinned SHA before a run. One persistent MCP session serves a whole
run. Wiki text never becomes evidence (R3): sections carry the page's claims and the text
needed to attribute those claims to sections; the resolver turns them into code spans.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dwb.retrieval.wiki_types import Section, SectionRef
from dwb.trace import StageRecord

WIKI_DIR = "openwiki"
H2 = re.compile(r"\n(?=## )")


class OpenWikiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- snapshots


class WikiSnapshot(BaseModel):
    snapshot_id: str = ""
    repo: str
    sha: str
    generator: str = "openwiki"
    version: str
    provider: str
    model: str
    effort: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    elapsed_s: float | None = None
    cost_usd: float | None = None
    tree_sha256: str
    pages: int
    claims: int
    generated_at: str | None = None
    registered_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def compute_id(self) -> str:
        parts = {k: getattr(self, k) for k in ("repo", "sha", "generator", "version",
                                               "provider", "model", "effort", "config",
                                               "elapsed_s", "cost_usd", "tree_sha256")}
        digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        return f"wiki-{self.repo}-{digest[:12]}"


def tree_sha256(wiki_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in wiki_dir.rglob("*") if x.is_file()):
        h.update(p.relative_to(wiki_dir).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def openwiki_version(command: str | None = None) -> str | None:
    """Version of the installed openwiki CLI (from its package.json), or None if absent."""
    exe = command or shutil.which("openwiki")
    if not exe:
        return None
    pkg = Path(exe).resolve().parents[2] / "package.json"
    return json.loads(pkg.read_text()).get("version", "unknown") if pkg.exists() else "unknown"


def describe_wiki(wiki: Path, repo: str, sha: str, *, provider: str, model: str, version: str,
                  effort: str | None = None, elapsed_s: float | None = None,
                  cost_usd: float | None = None, config: dict[str, Any] | None = None,
                  generated_at: str | None = None) -> WikiSnapshot:
    """The snapshot record for an ``openwiki/`` tree. One definition of pages and claims for
    both the research registry and user mode."""
    claims = sum(len(json.loads(p.read_text()).get("claims", []))
                 for p in (wiki / ".claims").rglob("*.json"))
    pages = sum(1 for p in wiki.rglob("*.md")
                if p.name not in ("index.md", "INSTRUCTIONS.md") and ".claims" not in p.parts)
    snap = WikiSnapshot(
        repo=repo, sha=sha, version=version, provider=provider, model=model, effort=effort,
        config=config or {}, elapsed_s=elapsed_s, cost_usd=cost_usd,
        tree_sha256=tree_sha256(wiki), pages=pages, claims=claims, generated_at=generated_at,
    )
    snap.snapshot_id = snap.compute_id()
    return snap


def _head(checkout: Path) -> str:
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def register_snapshot(
    checkout: str | Path,
    repo: str,
    sha: str,
    out_root: str | Path,
    *,
    provider: str,
    model: str,
    version: str,
    effort: str | None = None,
    elapsed_s: float | None = None,
    cost_usd: float | None = None,
    config: dict[str, Any] | None = None,
) -> WikiSnapshot:
    """Copy ``<checkout>/openwiki`` into ``<out_root>/<snapshot_id>/`` with ``snapshot.json``."""
    checkout = Path(checkout)
    wiki = checkout / WIKI_DIR
    if not wiki.is_dir():
        raise OpenWikiError(f"no {WIKI_DIR}/ in {checkout}")
    if _head(checkout) != sha:
        raise OpenWikiError(f"{checkout} HEAD is not the pinned SHA {sha}")
    last = {}
    if (wiki / ".last-update.json").exists():
        last = json.loads((wiki / ".last-update.json").read_text())
        if last.get("gitHead") and last["gitHead"] != sha:
            raise OpenWikiError(f"wiki was generated at {last['gitHead']}, not {sha}")
        if last.get("model") and last["model"] != model:
            raise OpenWikiError(f"wiki was generated with {last['model']}, not {model}")
    snap = describe_wiki(wiki, repo, sha, provider=provider, model=model, version=version,
                         effort=effort, elapsed_s=elapsed_s, cost_usd=cost_usd, config=config,
                         generated_at=last.get("updatedAt"))
    dest = Path(out_root) / snap.snapshot_id
    if not (dest / "snapshot.json").exists():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(wiki, dest / WIKI_DIR)
        (dest / "snapshot.json").write_text(snap.model_dump_json(indent=2))
    return WikiSnapshot.model_validate_json((dest / "snapshot.json").read_text())


def restore_snapshot(snapshot_dir: str | Path, checkout: str | Path) -> WikiSnapshot:
    """Make ``<checkout>/openwiki`` equal to the snapshot, at the snapshot's SHA."""
    snapshot_dir, checkout = Path(snapshot_dir), Path(checkout)
    snap = WikiSnapshot.model_validate_json((snapshot_dir / "snapshot.json").read_text())
    if _head(checkout) != snap.sha:
        raise OpenWikiError(f"{checkout} HEAD is not the snapshot SHA {snap.sha}")
    target = checkout / WIKI_DIR
    if target.exists() and tree_sha256(target) == snap.tree_sha256:
        return snap
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(snapshot_dir / WIKI_DIR, target)
    if tree_sha256(target) != snap.tree_sha256:
        raise OpenWikiError("restored wiki does not match the snapshot tree hash")
    return snap


# --------------------------------------------------------------------------- parsing


def _payload(result: Any) -> dict:
    """Accept a raw MCP CallToolResult (object or dumped dict) and return its JSON payload."""
    d = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if d.get("isError"):
        texts = " ".join(c.get("text", "") for c in d.get("content", []))
        raise OpenWikiError(texts or "openwiki tool error")
    if d.get("structuredContent") is not None:
        return d["structuredContent"]
    for c in d.get("content", []):
        if c.get("type") == "text":
            return json.loads(c["text"])
    return {}


def parse_search(result: Any) -> list[SectionRef]:
    refs = []
    for rank, r in enumerate(_payload(result).get("results", []), start=1):
        if r.get("kind", "section") != "section" or not r.get("ref"):
            continue
        page, _, anchor = r["ref"][0].partition("#")
        lines = (r.get("content") or "").splitlines()
        refs.append(SectionRef(page=page, anchor=anchor, rank=rank,
                               title=lines[0] if lines else None, wiki=r.get("wiki"),
                               raw={"content": r.get("content")}))
    return refs


def split_sections(markdown: str) -> list[str]:
    """``##`` sections of a page body (frontmatter and preamble dropped)."""
    return [s.strip() for s in H2.split(markdown)[1:]]


def load_page_claims(root: Path, page: str) -> list[dict]:
    rel = Path(page).relative_to(WIKI_DIR).with_suffix(".json")
    p = root / WIKI_DIR / ".claims" / rel
    return json.loads(p.read_text()).get("claims", []) if p.exists() else []


def parse_read(result: Any, refs: list[SectionRef], root: Path) -> list[Section]:
    """Build sections with the page's claims (statements from the sidecar) and the page's
    other section texts, which the resolver needs to attribute page-level claims."""
    d = _payload(result)
    page = d["page"] if d["page"].startswith(f"{WIKI_DIR}/") else f"{WIKI_DIR}/{d['page']}"
    by_anchor = {r.anchor: r for r in refs}
    prov = d.get("provenance") or {}
    sidecar = {c["id"]: c for c in load_page_claims(root, page)}
    claims = []
    for c in prov.get("claims", []):
        claims.append({"id": c["id"], "statement": sidecar.get(c["id"], {}).get("statement", ""),
                       "evidence": [e["resource"] for e in c.get("evidence", [])]})
    page_md = (root / page).read_text() if (root / page).exists() else ""
    page_sections = split_sections(page_md)
    out = []
    for s in d.get("sections", []):
        ref = by_anchor.get(s["section"]) or SectionRef(page=page, anchor=s["section"],
                                                         rank=len(refs) + 1)
        text = s.get("content", "")
        idx = next((i for i, t in enumerate(page_sections) if t == text.strip()), None)
        out.append(Section(
            ref=ref, text=text,
            claim_evidence=[r for c in claims for r in c["evidence"]],
            sources=[s["resource"] if isinstance(s, dict) else str(s)
                     for s in prov.get("sources", [])],
            raw={"page_claims": claims, "page_sections": page_sections, "section_index": idx},
        ))
    return out


# --------------------------------------------------------------------------- client


def default_openwiki_bin() -> str:
    found = shutil.which("openwiki")
    if not found:
        raise OpenWikiError("openwiki executable not found on PATH")
    return found


class OpenWikiClient:
    """``async with OpenWikiClient(root) as wiki: await wiki.search(q, k)``."""

    def __init__(self, root: str | Path, *, command: str | None = None, host: str = "dwb",
                 call_timeout_s: float = 120.0):
        self.root = Path(root).resolve()
        self.command = command or default_openwiki_bin()
        self.host = host
        self.call_timeout_s = call_timeout_s  # a hung server fails the call, not the whole run
        self.startup_stage: StageRecord | None = None
        self._stack: AsyncExitStack | None = None
        self._session = None
        fd, name = tempfile.mkstemp(prefix="openwiki-mcp-", suffix=".log")
        os.close(fd)  # the server's shell appends to it by name
        self._errlog = Path(name)

    def _connection(self) -> dict:
        path = os.pathsep.join([str(Path(self.command).parent), "/usr/bin", "/bin",
                                "/usr/local/bin"])  # the server shells out to git
        return {
            "transport": "stdio",
            "command": "/bin/sh",
            "args": ["-c", 'exec "$0" mcp --host "$1" 2>>"$2"', self.command, self.host,
                     str(self._errlog)],
            "env": {"PATH": path, "HOME": os.environ.get("HOME", ""),
                    "OPENWIKI_TELEMETRY_DISABLED": "1"},
        }

    def stderr(self) -> str:
        return self._errlog.read_text(errors="replace") if self._errlog.exists() else ""

    async def __aenter__(self) -> OpenWikiClient:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools

        t0 = time.perf_counter()
        self._stack = AsyncExitStack()
        try:
            client = MultiServerMCPClient({"openwiki": self._connection()})
            self._session = await self._stack.enter_async_context(client.session("openwiki"))
            tools = {t.name for t in await load_mcp_tools(self._session)}
        except Exception as e:
            await self._stack.aclose()
            raise OpenWikiError(f"OpenWiki MCP server failed to start: {e}\nstderr:\n"
                                f"{self.stderr()}") from e
        missing = {"openwiki_search", "openwiki_read"} - tools
        if missing:
            await self._stack.aclose()
            raise OpenWikiError(f"OpenWiki MCP server lacks {sorted(missing)} (needs 0.9.0)")
        self.startup_stage = StageRecord(name="wiki_server_start",
                                         latency_ms=(time.perf_counter() - t0) * 1000,
                                         extra={"command": self.command, "tools": sorted(tools)})
        return self

    async def __aexit__(self, exc_type, *exc: object) -> None:
        if self._stack:
            await self._stack.aclose()
        if exc_type is None:  # keep the server log only when something went wrong
            self._errlog.unlink(missing_ok=True)

    async def _call(self, tool: str, args: dict) -> Any:
        if self._session is None:
            raise OpenWikiError("client not started; use 'async with'")
        try:
            return await asyncio.wait_for(
                self._session.call_tool(tool, {"root": str(self.root), **args}),
                self.call_timeout_s)
        except TimeoutError:
            raise OpenWikiError(f"{tool} did not answer within {self.call_timeout_s:.0f} s "
                                f"(server log: {self._errlog})") from None

    async def search(self, query: str, k: int) -> list[SectionRef]:
        return parse_search(await self._call("openwiki_search", {"query": query, "limit": k}))

    async def read(self, refs: list[SectionRef]) -> list[Section]:
        by_page: dict[str, list[SectionRef]] = {}
        for r in refs:
            by_page.setdefault(r.page, []).append(r)
        sections: list[Section] = []
        for page, page_refs in by_page.items():
            res = await self._call("openwiki_read", {
                "page": page, "sections": [r.anchor for r in page_refs],
                "includeProvenance": True})
            sections.extend(parse_read(res, page_refs, self.root))
        return sorted(sections, key=lambda s: s.ref.rank)
