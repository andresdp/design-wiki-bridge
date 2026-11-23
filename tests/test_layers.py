"""The module layers in docs/architecture.md §2: imports only point to the same or a lower layer.

The query pipeline and the evaluator sit side by side and never import each other.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dwb"

# Keep in sync with the module view in docs/architecture.md.
LAYERS: dict[str, tuple[int, str]] = {
    # 1 interface
    "cli": (1, ""), "chat": (1, ""), "init_wizard": (1, ""), "ui": (1, ""),
    # 2 application
    "project": (2, ""), "workspace": (2, ""), "runner": (2, ""), "eval.command": (2, ""),
    "curation": (2, ""),
    # 3 pipeline | evaluation (siblings)
    "graph": (3, "pipeline"), "merge": (3, "pipeline"), "rerank": (3, "pipeline"),
    "budget": (3, "pipeline"), "generate": (3, "pipeline"), "sufficiency": (3, "pipeline"),
    "eval.matching": (3, "evaluation"), "eval.recall": (3, "evaluation"),
    "eval.claims_judge": (3, "evaluation"), "eval.deepeval_diag": (3, "evaluation"),
    "eval.report": (3, "evaluation"), "evidence_text": (3, "evaluation"),
    # 4 adapters
    "retrieval.chunking": (4, ""), "retrieval.vector_store": (4, ""),
    "retrieval.code_index": (4, ""), "retrieval.pdf_index": (4, ""),
    "retrieval.openwiki_client": (4, ""), "retrieval.resolver": (4, ""),
    "retrieval.wiki_types": (4, ""), "web.tavily_store": (4, ""),
    # 5 domain model
    "evidence": (5, ""), "trace": (5, ""), "manifest": (5, ""), "prompts": (5, ""),
    "benchmark.schemas": (5, ""), "benchmark.swe_qa": (5, ""), "benchmark.freeze": (5, ""),
    # 6 foundation
    "repo": (6, ""), "tokens": (6, ""), "usage": (6, ""), "log": (6, ""), "fatal": (6, ""),
    "progress": (6, ""),
}


def _module(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    return _imports_of(path.read_text())


def _imports_of(source: str) -> set[str]:
    """The dwb modules a source file imports, including ``from dwb import ui`` forms."""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            # `from dwb.x import y`: dwb.x, plus dwb.x.y when y is itself a module
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            names = []
        out |= {n.removeprefix("dwb.") for n in names if n.startswith("dwb.")}
    return {m for m in out if m in LAYERS}


def test_from_dwb_import_module_is_seen():
    assert _imports_of("from dwb import ui, __version__") == {"ui"}
    assert _imports_of("from dwb.retrieval import vector_store") == {"retrieval.vector_store"}
    assert _imports_of("from dwb.ui import out\nimport dwb.log") == {"ui", "log"}


def test_every_module_has_a_layer():
    modules = {_module(p) for p in SRC.rglob("*.py") if p.name != "__init__.py"}
    assert modules == set(LAYERS), "update LAYERS and the module view in docs/architecture.md"


def test_imports_point_down_and_pipeline_and_evaluation_stay_apart():
    bad = []
    for p in SRC.rglob("*.py"):
        if p.name == "__init__.py":
            continue
        src = _module(p)
        lvl, side = LAYERS[src]
        for dst in _imports(p):
            dlvl, dside = LAYERS[dst]
            if dlvl < lvl or (dlvl == lvl and side and dside and side != dside):
                bad.append(f"{src} (layer {lvl}) imports {dst} (layer {dlvl})")
    assert not bad, bad
