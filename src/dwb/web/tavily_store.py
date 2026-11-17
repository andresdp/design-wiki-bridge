"""Tavily record/replay store (R9, KTD8).

Only the SDK ``search`` and ``extract`` calls are used. ``include_answer`` is always off,
because Tavily's answer is model-synthesized and cannot be traced back to a source.

Modes:
- ``record``: call live, persist request + raw response + capture time (an existing record
  is returned unchanged, so the frozen pool is never silently refreshed);
- ``replay``: read only; a miss raises ``ReplayMiss``;
- ``live``: call only, nothing persisted (latency measurement flag).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from dwb.evidence import EvidenceItem, WebLocator
from dwb.log import get_logger

LOG = get_logger("web")

Mode = Literal["record", "replay", "live"]


class TavilyLike(Protocol):
    def search(self, query: str, **kwargs: Any) -> dict: ...
    def extract(self, urls: list[str], **kwargs: Any) -> dict: ...


class ReplayMiss(KeyError):
    def __init__(self, key: str, request: dict):
        super().__init__(key)
        self.key = key
        self.request = request

    def __str__(self) -> str:
        return f"Tavily replay miss for request key {self.key}: {json.dumps(self.request)}"


def normalize_search(
    query: str,
    *,
    search_depth: str = "basic",
    max_results: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "op": "search",
        "query": " ".join(query.split()),
        "search_depth": search_depth,
        "max_results": int(max_results),
        "include_domains": sorted({d.strip().lower() for d in include_domains or []}),
        "exclude_domains": sorted({d.strip().lower() for d in exclude_domains or []}),
    }


def normalize_extract(urls: list[str], *, extract_depth: str = "basic") -> dict[str, Any]:
    return {"op": "extract", "urls": sorted(set(urls)), "extract_depth": extract_depth}


def is_excluded(url: str, domains: list[str]) -> bool:
    """True when the URL's host is one of ``domains`` or a subdomain of one."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in domains))


def request_key(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


class TavilyStore:
    def __init__(self, root: str | Path, store_id: str, mode: Mode = "replay",
                 client: TavilyLike | None = None):
        self.dir = Path(root) / store_id
        self.store_id = store_id
        self.mode = mode
        self._client = client
        self.last_call: dict[str, Any] = {}

    def _get_client(self) -> TavilyLike:
        if self._client is None:
            from tavily import TavilyClient

            self._client = TavilyClient()
        return self._client

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def _fetch(self, request: dict[str, Any]) -> dict[str, Any]:
        key = request_key(request)
        path = self._path(key)
        t0 = time.perf_counter()
        if self.mode in ("replay", "record") and path.exists():
            try:
                rec = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise ValueError(f"corrupt Tavily record {path} ({e}); delete it and rerun "
                                 "with --web-mode record") from None
            self.last_call = {"key": key, "source": "store", "latency_ms": 0.0}
            LOG.info("    web: replayed %s (captured %s)", key[:12], rec["captured_at"][:10])
            return rec
        if self.mode == "replay":
            LOG.warning("    web: [red]replay miss[/] for %s (rerun with --web-mode record)",
                        key[:12])
            raise ReplayMiss(key, request)
        LOG.info("    web: live Tavily %s (%s mode)", request["op"], self.mode)
        client = self._get_client()
        params = {k: v for k, v in request.items() if k != "op"}
        if request["op"] == "search":
            for k in ("include_domains", "exclude_domains"):
                if not params[k]:
                    params.pop(k)
            response = client.search(params.pop("query"), include_answer=False, **params)
        else:
            response = client.extract(params.pop("urls"), **params)
        latency = (time.perf_counter() - t0) * 1000
        captured_at = datetime.now(UTC).isoformat()
        rec = {
            "key": key,
            "request": request,
            "response": response,
            "captured_at": captured_at,
            "capture_id": f"cap_{key[:12]}_{captured_at[:10]}",
        }
        if self.mode == "record":
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")  # a torn write must never be replayed
            tmp.write_text(json.dumps(rec, indent=2, sort_keys=True))
            os.replace(tmp, path)
        self.last_call = {"key": key, "source": "live", "latency_ms": latency}
        return rec

    def search(self, query: str, **kwargs: Any) -> list[EvidenceItem]:
        request = normalize_search(query, **kwargs)
        rec = self._fetch(request)
        excluded = request["exclude_domains"]
        results = rec["response"].get("results", [])
        # Tavily applies exclude_domains on its side; filter again here so a subdomain or a
        # result the service let through can never cite the project's own sites (spike §8)
        dropped = [r["url"] for r in results if is_excluded(r.get("url", ""), excluded)]
        if dropped:
            LOG.info("    web: dropped %d result(s) on excluded domains", len(dropped))
        self.last_call = {**self.last_call, "excluded_urls": dropped}
        return [
            EvidenceItem(
                source_type="web",
                text=r.get("content") or "",
                locator=WebLocator(url=r["url"], capture_id=rec["capture_id"], passage_index=i),
                provenance={"branch": "web", "rank": i + 1, "score": r.get("score"),
                            "title": r.get("title"), "captured_at": rec["captured_at"],
                            "request_key": rec["key"]},
            )
            for i, r in enumerate(results)  # i is the capture position: IDs stay stable
            if r.get("content") and r["url"] not in dropped
        ]

    def extract(self, urls: list[str], **kwargs: Any) -> list[EvidenceItem]:
        rec = self._fetch(normalize_extract(urls, **kwargs))
        return [
            EvidenceItem(
                source_type="web",
                text=r.get("raw_content") or "",
                locator=WebLocator(url=r["url"], capture_id=rec["capture_id"], passage_index=0),
                provenance={"branch": "web", "captured_at": rec["captured_at"],
                            "request_key": rec["key"]},
            )
            for r in rec["response"].get("results", [])
            if r.get("raw_content")
        ]
