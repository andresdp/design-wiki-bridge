"""User mode: ``dwb`` inside any git repository, configured by ``<repo>/.dwb/config.yaml``.

``dwb init`` writes the config, ``dwb index`` builds every source it lists for the HEAD
commit, and ``dwb ask`` / ``dwb chat`` answer from those indexes. Everything dwb writes
lives in ``<repo>/.dwb/``, hidden from git through ``.git/info/exclude``. The research mode
(``configs/repos.yaml`` in design-wiki-bridge) is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dwb.log import get_logger
from dwb.prompts import GENERATE_PROMPT
from dwb.workspace import RepoInfo, Workspace, WorkspaceError

LOG = get_logger("project")

DWB_DIR = ".dwb"
CONFIG_NAME = "config.yaml"
STATE_NAME = "state.json"
WIKI_RECORD = "wiki.json"
GLOBAL_ENV = Path.home() / ".config" / "dwb" / ".env"

CODE_EXTENSIONS = [
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".sql",
    ".md", ".rst", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json",
]


# --------------------------------------------------------------------------- config


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelsCfg(_M):
    chat: str = "gpt-5.6-luna"
    embedding: str = "text-embedding-3-large"
    temperature: float = 0.0


class SourcesCfg(_M):
    wiki: bool = False
    pdf: bool = True
    web: bool = False


class CodeCfg(_M):
    extensions: list[str] = Field(default_factory=lambda: list(CODE_EXTENSIONS))
    exclude: list[str] = Field(default_factory=lambda: ["openwiki/", ".dwb/"])
    max_span_tokens: int = 1500


class PdfEntry(_M):
    source: str  # local path (relative to the repo root) or http(s) URL
    title: str = ""


class WebCfg(_M):
    exclude_domains: list[str] = Field(default_factory=list)
    max_results: int = 5
    search_depth: str = "basic"


class WikiCfg(_M):
    enabled: bool = False
    generate: bool = False  # run OpenWiki during `dwb index` when the wiki is missing/stale
    provider: str = "openai"
    model: str = "gpt-5.6-luna"


class ProjectConfig(_M):
    version: int = 1
    name: str
    models: ModelsCfg = Field(default_factory=ModelsCfg)
    sources: SourcesCfg = Field(default_factory=SourcesCfg)
    code: CodeCfg = Field(default_factory=CodeCfg)
    pdfs: list[PdfEntry] = Field(default_factory=list)
    web: WebCfg = Field(default_factory=WebCfg)
    wiki: WikiCfg = Field(default_factory=WikiCfg)

    def to_yaml(self) -> str:
        header = (
            "# dwb project configuration, written by `dwb init`. Edit freely, then run\n"
            "# `dwb index` again. Reference: docs/usage.md (design-wiki-bridge).\n"
            "#   models:  chat = answering + reranking model; embedding = index model\n"
            "#   sources: defaults for `dwb ask` / `dwb chat` (the repository is always on)\n"
            "#   pdfs:    local paths (relative to the repo root) or URLs\n"
            "#   wiki:    OpenWiki-guided repository search; generate = run OpenWiki in\n"
            "#            `dwb index` when openwiki/ is missing or older than HEAD\n"
        )
        return header + yaml.safe_dump(self.model_dump(), sort_keys=False)


# --------------------------------------------------------------------------- git helpers


def git(root: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def git_root(start: Path) -> Path | None:
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.returncode == 0 else None


def head_sha(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def is_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", "--untracked-files=no", check=False))


def find_project(start: Path | None = None) -> Path | None:
    """The git root of ``start`` if it has ``.dwb/config.yaml``."""
    root = git_root(start or Path.cwd())
    if root and (root / DWB_DIR / CONFIG_NAME).exists():
        return root
    return None


def hide_from_git(root: Path) -> None:
    """Add ``.dwb/`` to .git/info/exclude (the repo's own .gitignore is left alone)."""
    git_dir = Path(git(root, "rev-parse", "--git-common-dir"))
    excl = (git_dir if git_dir.is_absolute() else root / git_dir) / "info" / "exclude"
    excl.parent.mkdir(parents=True, exist_ok=True)
    text = excl.read_text() if excl.exists() else ""
    if f"/{DWB_DIR}/" not in text.split():
        excl.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                        + f"/{DWB_DIR}/\n")


def remote_url(root: Path) -> str:
    return git(root, "remote", "get-url", "origin", check=False)


def suggest_exclude_domains(root: Path) -> list[str]:
    """The project's own hosts: its git host, docs URLs in pyproject/package.json, PyPI."""
    domains: list[str] = []

    def add(host: str) -> None:
        host = host.lower().strip().removeprefix("www.")
        if host and host not in domains:
            domains.append(host)

    url = remote_url(root)
    m = re.search(r"(?:@|//)([^/:]+)[/:]", url)
    if m:
        add(m.group(1))
    text = ""
    for name in ("pyproject.toml", "setup.cfg", "setup.py", "package.json"):
        p = root / name
        if p.exists():
            text += p.read_text(errors="ignore")
    for host in re.findall(r"https?://([A-Za-z0-9.-]+\.(?:readthedocs\.io|readthedocs\.org|"
                           r"github\.io|palletsprojects\.com|[a-z]+\.(?:org|io|dev|com)))", text):
        if not host.endswith(("python.org", "opensource.org", "shields.io", "pypa.io")):
            add(host)
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        add("pypi.org")
    if (root / "package.json").exists():
        add("npmjs.com")
    return domains


# --------------------------------------------------------------------------- keys


def load_env() -> list[Path]:
    """Load ./.env then ~/.config/dwb/.env. Variables already set (the shell) always win."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return []
    loaded = []
    for p in (Path(".env"), GLOBAL_ENV):
        if p.exists() and load_dotenv(p, override=False):
            loaded.append(p)
    return loaded


def save_global_key(name: str, value: str, path: Path = GLOBAL_ENV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in (path.read_text().splitlines() if path.exists() else [])
             if not ln.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    os.environ[name] = value
    return path


# --------------------------------------------------------------------------- workspace


class ProjectWorkspace(Workspace):
    """The ``Workspace`` interface used by chat, backed by one repo's ``.dwb/``."""

    restores_wiki = False  # the wiki is used in place (openwiki/ in the repo)

    def __init__(self, root: Path):
        root = root.resolve()
        d = root / DWB_DIR
        super().__init__(root=root, index_root=d / "index", wiki_root=d, tavily_root=d / "tavily")
        self.dir = d
        cfg_path = d / CONFIG_NAME
        if not cfg_path.exists():
            raise WorkspaceError(f"{root} is not a dwb project ({cfg_path} not found). "
                                 "Run `dwb init` there.")
        try:
            self.config = ProjectConfig.model_validate(yaml.safe_load(cfg_path.read_text()) or {})
        except Exception as e:  # pydantic ValidationError or YAML error
            raise WorkspaceError(f"{cfg_path} is not valid: {e}") from None

    @property
    def state(self) -> dict[str, Any] | None:
        p = self.dir / STATE_NAME
        return json.loads(p.read_text()) if p.exists() else None

    def require_indexed(self) -> dict[str, Any]:
        st = self.state
        if not st:
            raise WorkspaceError(f"{self.config.name} has not been indexed yet. Run: dwb index")
        head = head_sha(self.root)
        if head != st["sha"]:
            LOG.warning("The index is for commit %s but HEAD is %s. Answers cite the indexed "
                        "commit; run [bold]dwb index[/] to update.", st["sha"][:12], head[:12])
        return st

    def repos(self) -> dict[str, RepoInfo]:
        st = self.state or {}
        return {self.config.name: RepoInfo(name=self.config.name, url=remote_url(self.root),
                                           sha=st.get("sha") or head_sha(self.root),
                                           checkout=self.root)}

    def repo(self, name: str | None = None) -> RepoInfo:
        return self.repos()[self.config.name]

    def chat_config(self) -> dict[str, Any]:
        c = self.config
        return {
            "models": {"generator": c.models.chat, "reranker": c.models.chat,
                       "embedding": c.models.embedding, "temperature": c.models.temperature},
            "sources": c.sources.model_dump(),
            "web": {"max_results": c.web.max_results, "search_depth": c.web.search_depth},
            "prompt": str(GENERATE_PROMPT),
            "repos": {c.name: {"pdf_corpus": str(self.dir / "pdf_corpus.yaml"),
                               "web_exclude_domains": c.web.exclude_domains}},
        }

    def find_wiki_snapshot(self, repo: str, sha: str) -> dict[str, Any] | None:
        p = self.dir / WIKI_RECORD
        if not p.exists():
            return None
        rec = json.loads(p.read_text())
        return rec if rec.get("sha") == sha else None

    def status_rows(self) -> list[tuple[str, str, str]]:
        """(source, state, detail) for `dwb status` in a project."""
        c, st = self.config, self.state or {}
        head = head_sha(self.root)
        rows = [("commit", "ok" if st.get("sha") == head else ("stale" if st else "missing"),
                 f"indexed {st['sha'][:12]}, HEAD {head[:12]}" if st else f"HEAD {head[:12]}")]
        rows.append(("code", "ok" if st.get("code_index_id") else "missing",
                     st.get("code_index_id") or "run dwb index"))
        if c.pdfs:
            rows.append(("pdf", "ok" if st.get("pdf_index_id") else "missing",
                         f"{len(c.pdfs)} documents, {st.get('pdf_index_id') or 'not indexed'}"))
        else:
            rows.append(("pdf", "off", "no PDFs configured"))
        if c.wiki.enabled:
            rows.append(("wiki", "ok" if st.get("wiki_snapshot_id") else "missing",
                         st.get("wiki_snapshot_id") or st.get("wiki_note") or "not indexed"))
        else:
            rows.append(("wiki", "off", "disabled in config"))
        rows.append(("web", "on" if c.sources.web else "off",
                     "excluding " + ", ".join(c.web.exclude_domains) if c.web.exclude_domains
                     else "no excluded domains"))
        return rows


# --------------------------------------------------------------------------- indexing


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "doc"


def _fetch_pdf(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _slug(Path(url.split("?")[0]).stem or url) + ".pdf"
    dest = dest_dir / name
    if dest.exists():
        return dest
    LOG.info("Downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "dwb/0.1 (pdf fetch)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if not data.startswith(b"%PDF"):
        raise WorkspaceError(f"{url} did not return a PDF (download it in a browser and add the "
                             "local file to pdfs: in .dwb/config.yaml instead)")
    dest.write_bytes(data)
    return dest


def openwiki_status(root: Path) -> dict[str, Any]:
    wiki = root / "openwiki"
    last = wiki / ".last-update.json"
    info: dict[str, Any] = {"exists": wiki.is_dir(), "git_head": None, "model": None}
    if last.exists():
        d = json.loads(last.read_text())
        info.update(git_head=d.get("gitHead"), model=d.get("model"), status=d.get("status"))
    return info


def openwiki_version() -> str | None:
    exe = shutil.which("openwiki")
    if not exe:
        return None
    pkg = Path(exe).resolve().parents[2] / "package.json"
    return json.loads(pkg.read_text()).get("version") if pkg.exists() else "unknown"


def _generate_wiki(root: Path, cfg: WikiCfg, update: bool) -> float:
    exe = shutil.which("openwiki")
    if not exe:
        raise WorkspaceError("openwiki is not installed (npm install -g openwiki@0.9.0)")
    env = {**os.environ, "OPENWIKI_TELEMETRY_DISABLED": "1",
           "OPENWIKI_PROVIDER": cfg.provider, "OPENWIKI_MODEL_ID": cfg.model}
    cmd = [exe, "code", "--update" if update else "--init"]
    LOG.info("Running OpenWiki (%s, %s/%s); this makes model calls and can take ~15 min",
             " ".join(cmd[1:]), cfg.provider, cfg.model)
    t0 = time.perf_counter()
    rc = subprocess.run(cmd, cwd=root, env=env).returncode
    if rc != 0:
        raise WorkspaceError(f"OpenWiki exited with status {rc}")
    return time.perf_counter() - t0


def index_project(ws: ProjectWorkspace, *, embedding=None, generate_wiki: bool | None = None,
                  ) -> dict[str, Any]:
    """Build every source the config lists, for the HEAD commit. Returns the new state."""
    from dwb.repo import RepoReader
    from dwb.retrieval.chunking import ChunkingConfig
    from dwb.retrieval.code_index import build_code_index
    from dwb.retrieval.openwiki_client import WikiSnapshot, tree_sha256
    from dwb.retrieval.pdf_index import PdfCorpus, PdfDoc, build_pdf_index

    c, root = ws.config, ws.root
    sha = head_sha(root)
    if is_dirty(root):
        LOG.warning("Uncommitted changes are not indexed: dwb indexes commit %s (HEAD).",
                    sha[:12])
    if embedding is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise WorkspaceError("OPENAI_API_KEY is not set (run dwb init, or export it)")
        from langchain_openai import OpenAIEmbeddings

        embedding = OpenAIEmbeddings(model=c.models.embedding)
    state: dict[str, Any] = {"sha": sha, "embedding_model": c.models.embedding,
                             "indexed_at": datetime.now(UTC).isoformat()}

    # code (always: the repository is the one source that is always on)
    LOG.info("[bold]Code[/]: %s at %s", c.name, sha[:12])
    cfg = ChunkingConfig(max_span_tokens=c.code.max_span_tokens,
                         extensions=tuple(c.code.extensions),
                         exclude_prefixes=tuple(c.code.exclude))
    idx = build_code_index(ws.index_root, RepoReader.from_git(root, sha), c.name, embedding,
                           c.models.embedding, cfg)
    state["code_index_id"] = idx.info.index_id

    # PDFs
    if c.pdfs:
        LOG.info("[bold]PDFs[/]: %d documents", len(c.pdfs))
        docs = []
        for e in c.pdfs:
            path = _fetch_pdf(e.source, ws.dir / "pdfs") if re.match(r"https?://", e.source) \
                else (root / e.source)
            if not path.exists():
                raise WorkspaceError(f"PDF not found: {e.source} (edit pdfs: in .dwb/config.yaml)")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            docs.append(PdfDoc(doc_id=_slug(e.title or path.stem), version=f"sha256:{digest[:12]}",
                               path=str(path.resolve()), title=e.title or path.stem))
        corpus = PdfCorpus(corpus_id=f"{_slug(c.name)}-pdfs", documents=docs)
        (ws.dir / "pdf_corpus.yaml").write_text(yaml.safe_dump(corpus.model_dump(),
                                                               sort_keys=False))
        pidx = build_pdf_index(ws.index_root, corpus, Path("/"), embedding, c.models.embedding)
        state["pdf_index_id"] = pidx.info.index_id
        state["pdf_corpus_id"] = corpus.corpus_id

    # wiki
    if c.wiki.enabled:
        ow = openwiki_status(root)
        current = ow["exists"] and ow["git_head"] == sha
        gen = c.wiki.generate if generate_wiki is None else generate_wiki
        elapsed = None
        if not current and gen:
            elapsed = _generate_wiki(root, c.wiki, update=ow["exists"])
            ow = openwiki_status(root)
            current = ow["exists"] and ow["git_head"] == sha
        if current:
            snap = WikiSnapshot(repo=c.name, sha=sha, version=openwiki_version() or "unknown",
                                provider=c.wiki.provider, model=ow["model"] or c.wiki.model,
                                elapsed_s=elapsed, tree_sha256=tree_sha256(root / "openwiki"),
                                pages=sum(1 for p in (root / "openwiki").rglob("*.md")
                                          if ".claims" not in p.parts),
                                claims=sum(len(json.loads(p.read_text()).get("claims", []))
                                           for p in (root / "openwiki/.claims").rglob("*.json")),
                                generated_at=None)
            snap.snapshot_id = snap.compute_id()
            (ws.dir / WIKI_RECORD).write_text(snap.model_dump_json(indent=2))
            state["wiki_snapshot_id"] = snap.snapshot_id
            LOG.info("[bold]Wiki[/]: %s (%d pages, %d claims)", snap.snapshot_id, snap.pages,
                     snap.claims)
        else:
            why = "missing" if not ow["exists"] else f"generated at {str(ow['git_head'])[:12]}"
            state["wiki_note"] = f"openwiki/ {why}, not HEAD; set wiki.generate: true or " \
                                 "run openwiki code --update"
            LOG.warning("Wiki not available: %s", state["wiki_note"])

    (ws.dir / STATE_NAME).write_text(json.dumps(state, indent=2))
    return state


def write_config(root: Path, cfg: ProjectConfig) -> Path:
    d = root / DWB_DIR
    d.mkdir(exist_ok=True)
    (d / CONFIG_NAME).write_text(cfg.to_yaml())
    hide_from_git(root)
    return d / CONFIG_NAME
