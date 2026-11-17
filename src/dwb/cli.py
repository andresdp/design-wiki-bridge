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


def cmd_wiki_snapshot(args: argparse.Namespace) -> int:
    """Register the openwiki/ tree generated in a pinned checkout as a snapshot (KTD6, R11).

    Generation itself is run by hand with the pinned environment (see the U1 spike doc):
    OPENWIKI_TELEMETRY_DISABLED=1 OPENWIKI_PROVIDER=... OPENWIKI_MODEL_ID=... openwiki code --init
    """
    import shutil

    from dwb.retrieval.openwiki_client import register_snapshot

    rc = _repo_cfg(args.repo)
    version = args.version
    if version is None:
        exe = shutil.which("openwiki")
        pkg = Path(exe).resolve().parents[2] / "package.json" if exe else None
        version = json.loads(pkg.read_text())["version"] if pkg and pkg.exists() else "unknown"
    snap = register_snapshot(
        rc["checkout"], args.repo, rc["sha"], args.root, provider=args.provider,
        model=args.model, version=version, effort=args.effort, elapsed_s=args.elapsed_s,
        cost_usd=args.cost_usd,
    )
    print(snap.model_dump_json(indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from dwb.manifest import load_manifest
    from dwb.runner import build_components, run_manifest

    m = load_manifest(args.manifest)
    if args.web_mode:
        m = m.model_copy(update={"web": m.web.model_copy(update={"mode": args.web_mode})})
        if args.web_mode == "live":
            m = m.model_copy(update={"run_id": f"{m.run_id}-live"})
    base = Path(".")
    comps = build_components(m, base_dir=base, index_root=Path(args.index_root),
                             tavily_root=Path(args.tavily_root))
    summary = run_manifest(m, comps, base_dir=base, runs_dir=Path(args.runs_dir),
                           overwrite=args.overwrite)
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 1


def cmd_check_paired(args: argparse.Namespace) -> int:
    from dwb.manifest import check_paired, load_manifest

    res = check_paired(load_manifest(args.a), load_manifest(args.b))
    print(json.dumps(res.model_dump(), indent=2))
    return 0 if res.ok else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    from dwb.eval.command import evaluate_runs

    res = evaluate_runs(Path(args.run_a), Path(args.run_b), Path(args.out),
                        config_path=Path(args.config), use_judge=not args.no_judge,
                        deepeval=args.deepeval or None)
    print(json.dumps(res, indent=2))
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

    ws = sub.add_parser("wiki-snapshot", help="register a generated OpenWiki tree as a snapshot")
    ws.add_argument("--repo", required=True, help="name in configs/repos.yaml")
    ws.add_argument("--provider", required=True)
    ws.add_argument("--model", required=True)
    ws.add_argument("--effort")
    ws.add_argument("--version", help="openwiki version (default: read from the installed CLI)")
    ws.add_argument("--elapsed-s", type=float)
    ws.add_argument("--cost-usd", type=float)
    ws.add_argument("--root", default="artifacts/wikis")
    ws.set_defaults(func=cmd_wiki_snapshot)

    rn = sub.add_parser("run", help="run one manifest (one condition) over its question set")
    rn.add_argument("--manifest", required=True)
    rn.add_argument("--runs-dir", default="runs")
    rn.add_argument("--index-root", default=str(INDEX_ROOT))
    rn.add_argument("--tavily-root", default="artifacts/tavily")
    rn.add_argument("--web-mode", choices=["record", "replay", "live"],
                    help="override web.mode; 'live' is the latency flag and renames the run")
    rn.add_argument("--overwrite", action="store_true")
    rn.set_defaults(func=cmd_run)

    cp = sub.add_parser("check-paired", help="paired-configuration gate for an A/B pair")
    cp.add_argument("a")
    cp.add_argument("b")
    cp.set_defaults(func=cmd_check_paired)

    ev = sub.add_parser("evaluate", help="offline evaluation of saved A and B traces")
    ev.add_argument("--run-a", required=True, help="run directory of condition A")
    ev.add_argument("--run-b", required=True, help="run directory of condition B")
    ev.add_argument("--out", required=True, help="report directory")
    ev.add_argument("--config", default="configs/eval.yaml")
    ev.add_argument("--no-judge", action="store_true", help="recall and cost only")
    ev.add_argument("--deepeval", action="store_true", help="also run DeepEval diagnostics")
    ev.set_defaults(func=cmd_evaluate)
    return p


def _load_dotenv() -> None:
    """Load API keys from ./.env (gitignored). Variables already set in the shell win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(".env"), override=False)


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
