"""``dwb`` command line: index-code, index-pdf, wiki-snapshot, run, evaluate."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

REPOS_YAML = Path("configs/repos.yaml")
INDEX_ROOT = Path("artifacts/indexes")


def _repo_cfg(name: str) -> dict:
    cfg = yaml.safe_load(REPOS_YAML.read_text())["repos"]
    if name not in cfg:
        raise SystemExit(f"unknown repo {name!r}; known: {sorted(cfg)}")
    return cfg[name]


def _embedding(model: str, fake: bool):
    if fake:
        from langchain_core.embeddings import DeterministicFakeEmbedding

        return DeterministicFakeEmbedding(size=64)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=model)


def cmd_index_code(args: argparse.Namespace) -> int:
    from dwb.repo import RepoReader
    from dwb.retrieval.chunking import ChunkingConfig
    from dwb.retrieval.code_index import build_code_index

    if args.path:
        reader = RepoReader.from_dir(args.path, sha=args.sha or "fixture")
        repo = args.repo
    else:
        rc = _repo_cfg(args.repo)
        reader = RepoReader.from_git(rc["checkout"], rc["sha"])
        repo = args.repo
    cfg = ChunkingConfig(max_span_tokens=args.max_span_tokens)
    idx = build_code_index(args.root, reader, repo, _embedding(args.embedding_model, args.fake),
                           args.embedding_model, cfg)
    print(json.dumps(asdict(idx.info), indent=2))
    return 0


def cmd_index_pdf(args: argparse.Namespace) -> int:
    from dwb.retrieval.pdf_index import PdfCorpus, build_pdf_index

    corpus = PdfCorpus.load(args.corpus)
    base = Path(args.base_dir) if args.base_dir else Path(args.corpus).parent
    idx = build_pdf_index(args.root, corpus, base, _embedding(args.embedding_model, args.fake),
                          args.embedding_model)
    print(json.dumps(asdict(idx.info), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dwb")
    sub = p.add_subparsers(dest="command", required=True)

    ic = sub.add_parser("index-code", help="build the condition A code index")
    ic.add_argument("--repo", required=True, help="name in configs/repos.yaml")
    ic.add_argument("--path", help="index a plain directory instead (fixtures)")
    ic.add_argument("--sha", help="commit label for --path")
    ic.add_argument("--embedding-model", default="text-embedding-3-large")
    ic.add_argument("--max-span-tokens", type=int, default=1500)
    ic.add_argument("--root", default=str(INDEX_ROOT))
    ic.add_argument("--fake", action="store_true", help="offline fake embeddings (smoke only)")
    ic.set_defaults(func=cmd_index_code)

    ip = sub.add_parser("index-pdf", help="build the shared PDF index")
    ip.add_argument("--corpus", required=True, help="PDF corpus YAML")
    ip.add_argument("--base-dir", help="directory PDF paths are relative to")
    ip.add_argument("--embedding-model", default="text-embedding-3-large")
    ip.add_argument("--root", default=str(INDEX_ROOT))
    ip.add_argument("--fake", action="store_true")
    ip.set_defaults(func=cmd_index_pdf)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
