"""Read repository files at a pinned revision.

Validation and span resolution read through ``git show <sha>:<path>`` so files that
OpenWiki (or anything else) added to a working checkout never count as evidence.
``RepoReader.from_dir`` reads a plain directory and is used for test fixtures.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


class RepoReader:
    def __init__(self, sha: str, *, git_dir: Path | None = None, root: Path | None = None):
        if (git_dir is None) == (root is None):
            raise ValueError("give exactly one of git_dir or root")
        self.sha = sha
        self.git_dir = git_dir
        self.root = root
        self._read = lru_cache(maxsize=512)(self._read_uncached)

    @classmethod
    def from_git(cls, repo_dir: str | Path, sha: str) -> RepoReader:
        repo_dir = Path(repo_dir)
        full = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", f"{sha}^{{commit}}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return cls(full, git_dir=repo_dir)

    @classmethod
    def from_dir(cls, root: str | Path, sha: str = "fixture") -> RepoReader:
        return cls(sha, root=Path(root))

    def _read_uncached(self, path: str) -> str | None:
        if self.root is not None:
            p = (self.root / path).resolve()
            if not p.is_relative_to(self.root.resolve()) or not p.is_file():
                return None
            return p.read_text(errors="replace")
        out = subprocess.run(
            ["git", "-C", str(self.git_dir), "show", f"{self.sha}:{path}"],
            capture_output=True, text=True,
        )
        return out.stdout if out.returncode == 0 else None

    def read(self, path: str) -> str | None:
        return self._read(path.removeprefix("./"))

    def exists(self, path: str) -> bool:
        return self.read(path) is not None

    def lines(self, path: str) -> list[str] | None:
        text = self.read(path)
        return None if text is None else text.splitlines()

    def line_count(self, path: str) -> int | None:
        lines = self.lines(path)
        return None if lines is None else len(lines)

    def span_text(self, path: str, start: int, end: int) -> str | None:
        lines = self.lines(path)
        if lines is None or start < 1 or end > len(lines) or start > end:
            return None
        return "\n".join(lines[start - 1 : end])

    def list_files(self) -> list[str]:
        if self.root is not None:
            return sorted(
                p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()
            )
        out = subprocess.run(
            ["git", "-C", str(self.git_dir), "ls-tree", "-r", "--name-only", self.sha],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.splitlines()
