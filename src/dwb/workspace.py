"""Project workspace: configured repositories and the artifacts built for them.

``dwb ask`` / ``dwb chat`` / ``dwb status`` discover indexes and wiki snapshots here, by
repository, pinned SHA, and embedding model, so users never handle index IDs by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPOS_YAML = Path("configs/repos.yaml")
CHAT_YAML = Path("configs/chat.yaml")
INDEX_ROOT = Path("artifacts/indexes")
WIKI_ROOT = Path("artifacts/wikis")
TAVILY_ROOT = Path("artifacts/tavily")


class WorkspaceError(RuntimeError):
    """A user-facing setup problem, with the command that fixes it."""


@dataclass
class RepoInfo:
    name: str
    url: str
    sha: str
    checkout: Path
    tag: str | None = None
    role: str | None = None

    @property
    def checkout_ok(self) -> bool:
        return (self.checkout / ".git").exists()


@dataclass
class Workspace:
    root: Path = Path(".")
    repos_yaml: Path = REPOS_YAML
    chat_yaml: Path = CHAT_YAML
    index_root: Path = INDEX_ROOT
    wiki_root: Path = WIKI_ROOT
    tavily_root: Path = TAVILY_ROOT
    _chat: dict[str, Any] = field(default_factory=dict, repr=False)
    restores_wiki = True  # research mode copies the pinned snapshot into the checkout

    def _p(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p

    # -- configuration --------------------------------------------------------------

    def repos(self) -> dict[str, RepoInfo]:
        path = self._p(self.repos_yaml)
        if not path.exists():
            raise WorkspaceError(
                f"{path} not found. Run dwb from the design-wiki-bridge folder, or create it "
                "(see docs/usage.md, 'Adding a repository')."
            )
        cfg = yaml.safe_load(path.read_text()).get("repos", {})
        return {
            name: RepoInfo(name=name, url=r["url"], sha=r["sha"],
                           checkout=self._p(Path(r["checkout"])), tag=r.get("tag"),
                           role=r.get("role"))
            for name, r in cfg.items()
        }

    def repo(self, name: str) -> RepoInfo:
        repos = self.repos()
        if name not in repos:
            raise WorkspaceError(f"unknown repository {name!r}. Configured: "
                                 f"{', '.join(sorted(repos)) or 'none'} (configs/repos.yaml).")
        return repos[name]

    def chat_config(self) -> dict[str, Any]:
        if not self._chat:
            path = self._p(self.chat_yaml)
            self._chat = yaml.safe_load(path.read_text()) if path.exists() else {}
        return self._chat

    def repo_chat_config(self, name: str) -> dict[str, Any]:
        return (self.chat_config().get("repos") or {}).get(name, {})

    # -- artifact discovery ---------------------------------------------------------

    def _indexes(self, kind: str) -> list[dict[str, Any]]:
        root = self._p(self.index_root)
        out = []
        for p in sorted(root.glob("*/index.json")) if root.exists() else []:
            info = json.loads(p.read_text())
            if info.get("kind") == kind:
                out.append(info)
        return out

    def find_code_index(self, repo: str, sha: str, embedding_model: str) -> str | None:
        hits = [i for i in self._indexes("code")
                if i["parts"].get("repo") == repo and i["parts"].get("sha") == sha
                and i["embedding_model"] == embedding_model]
        return max(hits, key=lambda i: i.get("created_at", ""))["index_id"] if hits else None

    def find_pdf_index(self, corpus_id: str, embedding_model: str) -> str | None:
        hits = [i for i in self._indexes("pdf")
                if i["parts"].get("corpus_id") == corpus_id
                and i["embedding_model"] == embedding_model]
        return max(hits, key=lambda i: i.get("created_at", ""))["index_id"] if hits else None

    def find_wiki_snapshot(self, repo: str, sha: str) -> dict[str, Any] | None:
        root = self._p(self.wiki_root)
        snaps = []
        for p in sorted(root.glob("*/snapshot.json")) if root.exists() else []:
            s = json.loads(p.read_text())
            if s.get("repo") == repo and s.get("sha") == sha:
                snaps.append(s)
        return max(snaps, key=lambda s: s.get("registered_at", "")) if snaps else None

    def pdf_corpus_id(self, repo: str) -> str | None:
        corpus = self.repo_chat_config(repo).get("pdf_corpus")
        if not corpus or not self._p(Path(corpus)).exists():
            return None
        return yaml.safe_load(self._p(Path(corpus)).read_text()).get("corpus_id")

    def status(self, embedding_model: str) -> list[dict[str, Any]]:
        rows = []
        for name, r in self.repos().items():
            corpus = self.pdf_corpus_id(name)
            snap = self.find_wiki_snapshot(name, r.sha)
            rows.append({
                "repo": name, "tag": r.tag or r.sha[:12], "checkout": r.checkout_ok,
                "code_index": self.find_code_index(name, r.sha, embedding_model),
                "pdf_index": self.find_pdf_index(corpus, embedding_model) if corpus else None,
                "pdf_corpus": corpus,
                "wiki": snap["snapshot_id"] if snap else None,
            })
        return rows
