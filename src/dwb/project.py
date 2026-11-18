"""User mode: ``dwb`` inside any git repository, configured by ``<repo>/.dwb/config.yaml``.

``dwb init`` writes the config, ``dwb index`` builds every source it lists for the HEAD
commit, and ``dwb ask`` / ``dwb chat`` answer from those indexes. Everything dwb writes
lives in ``<repo>/.dwb/``, hidden from git through ``.git/info/exclude``. The research mode
(``configs/repos.yaml`` in design-wiki-bridge) is unchanged.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dwb.log import get_logger
from dwb.progress import OnProgress, report
from dwb.prompts import GENERATE_PROMPT
from dwb.repo import GIT_TIMEOUT_S
from dwb.retrieval.openwiki_client import openwiki_version  # noqa: F401  (re-exported)
from dwb.workspace import ChatDefaults, RepoInfo, Workspace, WorkspaceError

LOG = get_logger("project")

DWB_DIR = ".dwb"
CONFIG_NAME = "config.yaml"
STATE_NAME = "state.json"
WIKI_RECORD = "wiki.json"
OPENWIKI_LOG = "openwiki.log"
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
    # a local file or folder (relative to the repo root) or an http(s) URL; a folder means
    # every .pdf inside it, subfolders included
    source: str
    title: str = ""  # ignored for folders (each file is titled by its name)


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
            "#   pdfs:    local files or folders (relative to the repo root) or URLs\n"
            "#   wiki:    OpenWiki-guided repository search; generate = run OpenWiki in\n"
            "#            `dwb index` when openwiki/ is missing or older than HEAD\n"
        )
        return header + yaml.safe_dump(self.model_dump(), sort_keys=False)


# --------------------------------------------------------------------------- git helpers


def git(root: Path, *args: str, check: bool = True) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                             timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git {' '.join(args)} did not finish within {GIT_TIMEOUT_S} s "
                             f"in {root}") from None
    if check and out.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def git_root(start: Path) -> Path | None:
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    return Path(out.stdout.strip()) if out.returncode == 0 else None


def head_sha(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def is_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", "--untracked-files=no", check=False))


def wiki_dirty(root: Path) -> bool:
    """Committed openwiki/ files edited since HEAD (an untracked wiki is the normal case)."""
    return bool(git(root, "status", "--porcelain", "--untracked-files=no", "--", "openwiki",
                    check=False))


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


# A repository's own .env is untrusted in user mode: a cloned repo could set
# OPENAI_BASE_URL (or a proxy) and send the user's key elsewhere. Outside the research
# harness folder only these names are read from ./.env.
LOCAL_ENV_KEYS = ("OPENAI_API_KEY", "TAVILY_API_KEY")


def load_env(cwd: Path | None = None, global_env: Path | None = None) -> list[Path]:
    """Load ./.env then ~/.config/dwb/.env. Variables already set (the shell) always win.

    ~/.config/dwb/.env is the user's own file and is loaded in full. ./.env is loaded in full
    only in the research harness folder (it has configs/repos.yaml); anywhere else only
    ``LOCAL_ENV_KEYS`` are taken from it."""
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:  # pragma: no cover
        return []
    cwd = cwd or Path.cwd()
    global_env = global_env or GLOBAL_ENV
    loaded = []
    local = cwd / ".env"
    if local.exists():
        if (cwd / "configs" / "repos.yaml").exists():
            load_dotenv(local, override=False)
        else:
            for k, v in dotenv_values(local).items():
                if k in LOCAL_ENV_KEYS and v and k not in os.environ:
                    os.environ[k] = v
                elif k not in LOCAL_ENV_KEYS:
                    LOG.debug("Ignoring %s from %s (only %s are read from a project .env)", k,
                              local, ", ".join(LOCAL_ENV_KEYS))
        loaded.append(local)
    if global_env.exists() and load_dotenv(global_env, override=False):
        loaded.append(global_env)
    return loaded


def save_global_key(name: str, value: str, path: Path = GLOBAL_ENV) -> Path:
    """Write the key with 0600 from the start (no window where it is world-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [ln for ln in (path.read_text().splitlines() if path.exists() else [])
             if not ln.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
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

    def chat_config(self) -> ChatDefaults:
        c = self.config
        return ChatDefaults(
            models={"generator": c.models.chat, "reranker": c.models.chat,
                    "embedding": c.models.embedding, "temperature": c.models.temperature},
            sources=c.sources.model_dump(),
            web={"max_results": c.web.max_results, "search_depth": c.web.search_depth},
            prompt=str(GENERATE_PROMPT),
            # the span size the code index was cut with (checked when chat opens the index)
            budgets={"max_span_tokens": c.code.max_span_tokens},
            repos={c.name: {"pdf_corpus": str(self.dir / "pdf_corpus.yaml"),
                            "web_exclude_domains": c.web.exclude_domains}},
        )

    @property
    def history_path(self) -> Path:
        return self.dir / "chat_history"

    # Artifact discovery reads the IDs `dwb index` recorded in state.json, never "the newest
    # matching index": an older index left in .dwb/index must not be picked up.

    def _indexed(self, sha: str, embedding_model: str | None = None) -> dict[str, Any] | None:
        st = self.state
        if not st or st.get("sha") != sha:
            return None
        if embedding_model is not None and st.get("embedding_model") != embedding_model:
            return None
        return st

    def find_code_index(self, repo: str, sha: str, embedding_model: str) -> str | None:
        st = self._indexed(sha, embedding_model)
        return st.get("code_index_id") if st else None

    def pdf_corpus_id(self, repo: str) -> str | None:
        st = self.state
        return st.get("pdf_corpus_id") if st and self.config.pdfs else None

    def find_pdf_index(self, corpus_id: str, embedding_model: str) -> str | None:
        st = self.state
        if not st or st.get("pdf_corpus_id") != corpus_id \
                or st.get("embedding_model") != embedding_model:
            return None
        return st.get("pdf_index_id")

    def find_wiki_snapshot(self, repo: str, sha: str) -> dict[str, Any] | None:
        """The wiki recorded by `dwb index`, only if openwiki/ is still that tree: the wiki is
        read in place while code is resolved at the indexed commit, so a regenerated or edited
        wiki would silently point at different code."""
        from dwb.retrieval.openwiki_client import tree_sha256

        p, st = self.dir / WIKI_RECORD, self._indexed(sha)
        if not p.exists() or not st or not st.get("wiki_snapshot_id"):
            return None
        rec = json.loads(p.read_text())
        if rec.get("sha") != sha or rec.get("snapshot_id") != st["wiki_snapshot_id"]:
            return None
        wiki = self.root / "openwiki"
        if not wiki.is_dir() or tree_sha256(wiki) != rec.get("tree_sha256"):
            raise WorkspaceError(f"{wiki} changed since dwb index (it no longer matches wiki "
                                 f"{rec['snapshot_id']}). Run: dwb index (or --no-wiki)")
        return rec

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


def is_url(source: str) -> bool:
    return bool(re.match(r"https?://", source))


def folder_pdfs(folder: Path) -> list[Path]:
    """Every PDF under ``folder``, subfolders included, in a stable order."""
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


@dataclass
class _Pdf:
    path: Path
    title: str
    digest: str
    source: str  # the config entry it came from
    explicit: bool  # named directly (file or URL), not found inside a folder


def resolve_pdfs(root: Path, entries: list[PdfEntry], download_dir: Path) -> list[_Pdf]:
    """The config's PDF entries as distinct files: folders expanded, URLs downloaded.

    The same file named twice is a config mistake and an error; a file a folder also
    contains is kept once (an explicit entry's title wins).
    """
    out: list[_Pdf] = []
    by_digest: dict[str, _Pdf] = {}
    for e in entries:
        if is_url(e.source):
            items = [(_fetch_pdf(e.source, download_dir), e.title, True)]
        else:
            p = root / e.source
            if p.is_dir():
                files = folder_pdfs(p)
                if not files:
                    raise WorkspaceError(f"no PDF files in {e.source} (edit pdfs: in "
                                         ".dwb/config.yaml)")
                items = [(f, "", False) for f in files]
            elif p.exists():
                items = [(p, e.title, True)]
            else:
                raise WorkspaceError(f"PDF not found: {e.source} (edit pdfs: in "
                                     ".dwb/config.yaml)")
        for path, title, explicit in items:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            seen = by_digest.get(digest)
            if seen is None:
                by_digest[digest] = pdf = _Pdf(path, title, digest, e.source, explicit)
                out.append(pdf)
            elif explicit and seen.explicit:
                what = "more than once" if seen.source == e.source else \
                    f"twice: {e.source} is the same file as {seen.source}"
                raise WorkspaceError(f"PDF {e.source} is listed {what}. Remove the duplicate "
                                     "from pdfs: in .dwb/config.yaml")
            elif explicit:  # named directly after a folder already brought it in
                seen.title, seen.explicit = title or seen.title, True
    return out


def pdf_filename(url: str) -> str:
    """Readable stem plus a hash of the full URL, so two URLs never share one download."""
    stem = _slug(Path(url.split("?")[0]).stem or url)
    return f"{stem}-{hashlib.sha256(url.encode()).hexdigest()[:8]}.pdf"


def _fetch_pdf(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf_filename(url)
    if dest.exists():
        return dest
    LOG.info("Downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "dwb/0.1 (pdf fetch)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except (OSError, ValueError) as e:  # URLError, HTTPError and timeouts are OSErrors
        raise WorkspaceError(f"could not download {url}: {e}") from None
    if not data.startswith(b"%PDF"):
        raise WorkspaceError(f"{url} did not return a PDF (download it in a browser and add the "
                             "local file to pdfs: in .dwb/config.yaml instead)")
    part = dest.with_name(dest.name + ".part")  # an interrupted download never looks complete
    part.write_bytes(data)
    os.replace(part, dest)
    return dest


def openwiki_status(root: Path) -> dict[str, Any]:
    wiki = root / "openwiki"
    last = wiki / ".last-update.json"
    info: dict[str, Any] = {"exists": wiki.is_dir(), "git_head": None, "model": None}
    if last.exists():
        d = json.loads(last.read_text())
        info.update(git_head=d.get("gitHead"), model=d.get("model"), status=d.get("status"))
    return info


def _tail(path: Path, n: int = 20) -> list[str]:
    """The last ``n`` lines of ``path``, read without loading the whole file."""
    try:
        with path.open(errors="replace") as fh:
            return [line.rstrip("\r\n") for line in collections.deque(fh, maxlen=n)]
    except OSError:
        return []


def _generate_wiki(root: Path, cfg: WikiCfg, update: bool, *, inherit_output: bool = False,
                   on_progress: OnProgress | None = None) -> float:
    """Run ``openwiki code`` in ``root``. Its stdout and stderr go to ``.dwb/openwiki.log``
    (KTD7), so a spinner can show meanwhile; ``inherit_output`` streams them to the terminal
    instead (``-v``). stdin is always inherited. A failure carries the log's last lines."""
    exe = shutil.which("openwiki")
    if not exe:
        raise WorkspaceError("openwiki is not installed (npm install -g openwiki@0.9.0)")
    env = {**os.environ, "OPENWIKI_TELEMETRY_DISABLED": "1",
           "OPENWIKI_PROVIDER": cfg.provider, "OPENWIKI_MODEL_ID": cfg.model}
    cmd = [exe, "code", "--update" if update else "--init"]
    log_path = None if inherit_output else root / DWB_DIR / OPENWIKI_LOG
    LOG.debug("Running OpenWiki (%s, %s/%s); this makes model calls and can take ~15 min",
              " ".join(cmd[1:]), cfg.provider, cfg.model)
    report(on_progress, "wiki", "start", label=" ".join(cmd[1:]),
           data={"log": log_path, "provider": cfg.provider, "model": cfg.model})
    t0 = time.perf_counter()
    if log_path is None:
        rc = subprocess.run(cmd, cwd=root, env=env).returncode
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as fh:
            rc = subprocess.run(cmd, cwd=root, env=env, stdout=fh,
                                stderr=subprocess.STDOUT).returncode
    if rc != 0:
        msg = f"OpenWiki exited with status {rc}"
        if log_path is not None:
            tail = _tail(log_path)
            msg += (":\n" + "\n".join(f"  {ln}" for ln in tail) if tail else "")
            msg += f"\nFull output: {log_path}"
        raise WorkspaceError(msg)
    elapsed = time.perf_counter() - t0
    report(on_progress, "wiki", "done", elapsed_s=elapsed, label=" ".join(cmd[1:]),
           data={"log": log_path})
    return elapsed


def index_project(ws: ProjectWorkspace, *, embedding=None, generate_wiki: bool | None = None,
                  on_progress: OnProgress | None = None, wiki_output: bool = False,
                  ) -> dict[str, Any]:
    """Build every source the config lists, for the HEAD commit. Returns the new state.

    ``on_progress`` receives the ``embed`` events of the code and PDF indexes and the ``wiki``
    events of OpenWiki generation. ``wiki_output`` streams OpenWiki's own output to the
    terminal instead of ``.dwb/openwiki.log``."""
    from dwb.repo import RepoReader
    from dwb.retrieval.chunking import ChunkingConfig
    from dwb.retrieval.code_index import build_code_index
    from dwb.retrieval.openwiki_client import describe_wiki
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

        from dwb.manifest import MODEL_NET

        embedding = OpenAIEmbeddings(model=c.models.embedding, **MODEL_NET)
    state: dict[str, Any] = {"sha": sha, "embedding_model": c.models.embedding,
                             "indexed_at": datetime.now(UTC).isoformat()}

    # PDFs are resolved first, so a config mistake stops before any embedding
    pdfs = resolve_pdfs(root, c.pdfs, ws.dir / "pdfs") if c.pdfs else []

    # code (always: the repository is the one source that is always on)
    LOG.info("[bold]Code[/]: %s at %s", c.name, sha[:12])
    cfg = ChunkingConfig(max_span_tokens=c.code.max_span_tokens,
                         extensions=tuple(c.code.extensions),
                         exclude_prefixes=tuple(c.code.exclude))
    idx = build_code_index(ws.index_root, RepoReader.from_git(root, sha), c.name, embedding,
                           c.models.embedding, cfg, on_progress=on_progress)
    state["code_index_id"] = idx.info.index_id

    # PDFs
    if pdfs:
        LOG.info("[bold]PDFs[/]: %d documents", len(pdfs))
        docs, used = [], set()
        for f in pdfs:
            title = f.title or f.path.stem
            doc_id, n = _slug(title), 1
            while doc_id in used:  # same name in two folders: keep citations unambiguous
                n += 1
                doc_id = f"{_slug(title)}-{n}"
            used.add(doc_id)
            docs.append(PdfDoc(doc_id=doc_id, version=f"sha256:{f.digest[:12]}",
                               path=str(f.path.resolve()), title=title))
        corpus = PdfCorpus(corpus_id=f"{_slug(c.name)}-pdfs", documents=docs)
        (ws.dir / "pdf_corpus.yaml").write_text(yaml.safe_dump(corpus.model_dump(),
                                                               sort_keys=False))
        pidx = build_pdf_index(ws.index_root, corpus, Path("/"), embedding, c.models.embedding,
                               on_progress=on_progress)
        state["pdf_index_id"] = pidx.info.index_id
        state["pdf_corpus_id"] = corpus.corpus_id
    else:  # PDFs removed from the config: drop the old corpus so chat cannot pick it up
        (ws.dir / "pdf_corpus.yaml").unlink(missing_ok=True)

    # wiki
    (ws.dir / WIKI_RECORD).unlink(missing_ok=True)  # re-recorded below only if still current
    if c.wiki.enabled:
        ow = openwiki_status(root)
        current = ow["exists"] and ow["git_head"] == sha
        gen = c.wiki.generate if generate_wiki is None else generate_wiki
        elapsed = None
        if not current and gen:
            elapsed = _generate_wiki(root, c.wiki, update=ow["exists"],
                                     inherit_output=wiki_output, on_progress=on_progress)
            ow = openwiki_status(root)
            current = ow["exists"] and ow["git_head"] == sha
        if current and wiki_dirty(root):
            current = False
            ow["dirty"] = True
        if current:
            snap = describe_wiki(root / "openwiki", c.name, sha,
                                 version=openwiki_version() or "unknown",
                                 provider=c.wiki.provider, model=ow["model"] or c.wiki.model,
                                 elapsed_s=elapsed)
            (ws.dir / WIKI_RECORD).write_text(snap.model_dump_json(indent=2))
            state["wiki_snapshot_id"] = snap.snapshot_id
            LOG.info("[bold]Wiki[/]: %s (%d pages, %d claims)", snap.snapshot_id, snap.pages,
                     snap.claims)
        else:
            why = ("missing" if not ow["exists"] else "has uncommitted edits" if ow.get("dirty")
                   else f"generated at {str(ow['git_head'])[:12]}")
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
