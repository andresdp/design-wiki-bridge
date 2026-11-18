"""Benchmark curation (``dwb benchmark``): draft, validate, freeze, and check question sets.

The commands only move and check records. People select the SWE-QA rows, write the custom
questions, and annotate claims and evidence spans (data/questions/README.md).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from dwb.benchmark.freeze import file_hash, find_overlaps, freeze
from dwb.benchmark.schemas import (
    QuestionRecord,
    load_records,
    save_records,
    validate_against_checkout,
)
from dwb.benchmark.swe_qa import Selection, build_drafts, fetch_split, load_split
from dwb.log import get_logger
from dwb.repo import RepoReader
from dwb.workspace import RepoInfo, Workspace, WorkspaceError

LOG = get_logger("curation")

DRAFTS_DIR = Path("data/questions/drafts")
DEV_DIR = Path("data/questions/dev")

Fetcher = Callable[[str, str, Path], Path]
ReaderFactory = Callable[[RepoInfo], RepoReader]


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


# ------------------------------------------------------------------------------ validate


@dataclass
class ValidationReport:
    path: Path
    records: list[QuestionRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def git_reader(repo: RepoInfo) -> RepoReader:
    """Reader on the pinned commit of a research checkout; a missing checkout is a setup error."""
    fix = (f"Clone {repo.url} at {repo.sha} into {repo.checkout} (see docs/usage.md, "
           "'Adding a research repository').")
    if not repo.checkout_ok:
        raise WorkspaceError(f"checkout for {repo.name} not found at {repo.checkout}. {fix}")
    try:
        return RepoReader.from_git(repo.checkout, repo.sha)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise WorkspaceError(f"{repo.checkout} does not contain the pinned commit "
                             f"{repo.sha}. {fix}") from None


def _parse(path: Path, report: ValidationReport) -> None:
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            report.records.append(QuestionRecord.model_validate_json(line))
        except ValidationError as e:
            report.problems.append(f"line {i}: {_validation_message(e)}")


class _PdfPages:
    """Page counts of each repository's corpus documents, read once per document."""

    def __init__(self, ws: Workspace, override: Path | None):
        self.ws, self.override = ws, override
        self.corpora: dict[str, dict[str, Path] | None] = {}
        self.pages: dict[Path, int | None] = {}
        self.reported: set[Path] = set()

    def corpus(self, repo: str) -> dict[str, Path] | None:
        if repo not in self.corpora:
            from dwb.retrieval.pdf_index import PdfCorpus

            rel = self.override or self.ws.chat_config().repo(repo).pdf_corpus
            path = self.ws.resolve(Path(rel)) if rel else None
            if path is None or not path.exists():
                self.corpora[repo] = None
            else:
                c = PdfCorpus.load(path)
                self.corpora[repo] = {d.doc_id: self.ws.resolve(Path(d.path))
                                      for d in c.documents}
        return self.corpora[repo]

    def count(self, pdf: Path) -> int | None:
        if pdf not in self.pages:
            if not pdf.exists():
                self.pages[pdf] = None
            else:
                import pymupdf

                with pymupdf.open(pdf) as doc:
                    self.pages[pdf] = doc.page_count
        return self.pages[pdf]


def _check_pdf_spans(r: QuestionRecord, pdfs: _PdfPages, problems: list[str]) -> None:
    spans = [a for req in r.evidence for a in req.alternatives if a.source_type == "pdf"]
    if not spans:
        return
    corpus = pdfs.corpus(r.repo)
    if corpus is None:
        problems.append(f"{r.id}: has PDF spans but {r.repo} has no pdf_corpus "
                        "(configs/chat.yaml, or pass --pdf-corpus)")
        return
    for a in spans:
        pdf = corpus.get(a.doc_id)
        if pdf is None:
            problems.append(f"{r.id}: PDF doc_id {a.doc_id!r} is not in the PDF corpus")
            continue
        n = pdfs.count(pdf)
        if n is None:
            if pdf not in pdfs.reported:
                pdfs.reported.add(pdf)
                problems.append(f"{r.id}: {pdf} is missing, so its pages cannot be checked "
                                "(see the corpus file for where it comes from)")
        elif a.page > n:
            problems.append(f"{r.id}: {a.doc_id} page {a.page} is past the end ({n} pages)")


def validate(ws: Workspace, path: Path, *, reader_for: ReaderFactory = git_reader,
             pdf_corpus: Path | None = None) -> ValidationReport:
    """Every problem in a record file, in one pass, plus summary counts.

    Code spans are checked at the pinned commit; PDF spans against the repository's corpus
    (``pdf_corpus`` in configs/chat.yaml, or ``pdf_corpus``). Readers are built only for
    repositories whose records have code spans.
    """
    report = ValidationReport(path=path)
    _parse(path, report)
    records = report.records
    seen: set[str] = set()
    for r in records:
        if r.id in seen:
            report.problems.append(f"{r.id}: duplicate id")
        seen.add(r.id)

    repos = ws.repos()
    readers: dict[str, RepoReader] = {}
    pdfs = _PdfPages(ws, pdf_corpus)
    for r in records:
        repo = repos.get(r.repo)
        if repo is None:
            report.problems.append(f"{r.id}: repository {r.repo!r} is not in configs/repos.yaml")
            continue
        if r.repo_sha != repo.sha:
            report.problems.append(f"{r.id}: repo_sha {r.repo_sha} is not the pinned "
                                   f"{r.repo} SHA {repo.sha}")
        if r.code_spans():
            if r.repo not in readers:
                readers[r.repo] = reader_for(repo)
            report.problems += [p for p in validate_against_checkout(r, readers[r.repo])
                                if "repo_sha" not in p]  # reported above
        _check_pdf_spans(r, pdfs, report.problems)

    report.counts = {
        "records": len(records),
        "public": sum(r.kind == "public" for r in records),
        "custom": sum(r.kind == "custom" for r in records),
        "without_evidence": sum(not r.evidence for r in records),
        "expected_claims": sum(len(r.expected_claims) for r in records),
    }
    return report


# -------------------------------------------------------------------------- freeze, check


@dataclass
class FreezeResult:
    report: ValidationReport
    digest: str | None = None

    @property
    def ok(self) -> bool:
        return self.digest is not None


def freeze_set(ws: Workspace, path: Path, *, reader_for: ReaderFactory = git_reader,
               pdf_corpus: Path | None = None, dev_dir: Path = DEV_DIR) -> FreezeResult:
    """Validate, check disjointness against each repository's dev set, then write the hash.

    Nothing is written when any problem or overlap exists.
    """
    report = validate(ws, path, reader_for=reader_for, pdf_corpus=pdf_corpus)
    for repo in sorted({r.repo for r in report.records}):
        dev_path = ws.resolve(dev_dir) / f"{repo}.jsonl"
        if dev_path.exists():
            test = [r for r in report.records if r.repo == repo]
            report.problems += [f"overlap with {dev_path.name} dev set: {o}"
                                for o in find_overlaps(load_records(dev_path), test)]
    if not report.ok:
        return FreezeResult(report)
    digest = freeze(path)
    LOG.info("Froze %s: %s", path, digest)
    return FreezeResult(report, digest)


@dataclass
class CheckResult:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_sets(dev_path: Path, test_path: Path) -> CheckResult:
    """Dev and test share no ID, question text, or SWE-QA row; a frozen test set is unchanged.

    A missing test file counts as an empty set (the test set may not exist yet).
    """
    if not dev_path.exists():
        raise WorkspaceError(f"dev set {dev_path} not found")
    res = CheckResult()
    if not test_path.exists():
        res.warnings.append(f"test set {test_path} does not exist yet; nothing to compare")
        return res
    res.problems += find_overlaps(load_records(dev_path), load_records(test_path))
    sidecar = Path(f"{test_path}.sha256")
    if sidecar.exists():
        frozen, actual = sidecar.read_text().strip(), file_hash(test_path)
        if frozen != actual:
            res.problems.append(f"{test_path} changed after it was frozen: {sidecar.name} has "
                                f"{frozen}, the file hashes to {actual}")
    return res
