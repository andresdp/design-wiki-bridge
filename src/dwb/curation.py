"""Benchmark curation (``dwb benchmark``): draft, validate, freeze, and check question sets.

The commands only move and check records. People select the SWE-QA rows, write the custom
questions, and annotate claims and evidence spans (data/questions/README.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dwb.benchmark.schemas import save_records
from dwb.benchmark.swe_qa import Selection, build_drafts, fetch_split, load_split
from dwb.log import get_logger
from dwb.workspace import Workspace, WorkspaceError

LOG = get_logger("curation")

DRAFTS_DIR = Path("data/questions/drafts")

Fetcher = Callable[[str, str, Path], Path]


@dataclass
class DraftResult:
    records_path: Path
    exclusions_path: Path
    n_records: int
    n_excluded: int


def _validation_message(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'record'}: {x['msg']}"
                     for x in e.errors())


def draft(ws: Workspace, selection_path: Path, *, out_dir: Path = DRAFTS_DIR,
          overwrite: bool = False, fetch: Fetcher = fetch_split) -> DraftResult:
    """Draft public records for a selection file's included rows, plus its exclusions.

    The selection must agree with the pins in ``configs/repos.yaml``. A missing split file is
    downloaded at the pinned revision; either way its sha256 must match the pin.
    """
    try:
        sel = Selection.load(selection_path)
    except ValidationError as e:
        raise WorkspaceError(f"invalid selection {selection_path}: {_validation_message(e)}") \
            from None
    repo = ws.repo(sel.repo)
    pin = ws.swe_qa_pin()
    split_file = f"{sel.split}.jsonl"
    expected = pin.sha256.get(split_file)
    if sel.dataset_revision != pin.revision:
        raise WorkspaceError(f"{selection_path}: dataset_revision {sel.dataset_revision} is not "
                             f"the pinned SWE-QA revision {pin.revision}")
    if sel.repo_sha != repo.sha:
        raise WorkspaceError(f"{selection_path}: repo_sha {sel.repo_sha} is not the pinned "
                             f"{sel.repo} SHA {repo.sha}")
    if repo.swe_qa_split and sel.split != repo.swe_qa_split:
        raise WorkspaceError(f"{selection_path}: split {sel.split!r} is not {sel.repo}'s "
                             f"swe_qa_split {repo.swe_qa_split!r} in configs/repos.yaml")
    if expected is None:
        raise WorkspaceError(f"configs/repos.yaml: no pinned sha256 for {split_file}")
    if sel.file_sha256 and sel.file_sha256 != expected:
        raise WorkspaceError(f"{selection_path}: file_sha256 differs from the pinned sha256 of "
                             f"{split_file} in configs/repos.yaml")

    local_dir = ws.resolve(pin.local_dir)
    path = local_dir / split_file
    if not path.exists():
        LOG.info("Downloading %s at SWE-QA revision %s", split_file, pin.revision[:12])
        local_dir.mkdir(parents=True, exist_ok=True)
        path = Path(fetch(split_file, pin.revision, local_dir))
    try:
        rows = load_split(path, sel.split, expected)
    except ValueError as e:
        raise WorkspaceError(f"{e}. Delete {path} and run draft again to download the pinned "
                             "file.") from None
    try:
        records, exclusions = build_drafts(sel, rows)
    except ValueError as e:
        raise WorkspaceError(f"{selection_path}: {e}") from None

    out = ws.resolve(out_dir)
    records_path = out / f"{sel.repo}.public.jsonl"
    exclusions_path = out / f"{sel.repo}.excluded.jsonl"
    existing = [p for p in (records_path, exclusions_path) if p.exists()]
    if existing and not overwrite:
        raise WorkspaceError(f"{existing[0]} exists and may hold annotations; pass --overwrite "
                             "to replace it")
    save_records(records, records_path)
    exclusions_path.write_text("".join(e.model_dump_json() + "\n" for e in exclusions))
    LOG.info("Drafted %d records and %d exclusions for %s", len(records), len(exclusions),
             sel.repo)
    return DraftResult(records_path, exclusions_path, len(records), len(exclusions))
