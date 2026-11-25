# Bronze: lands each new filing in the lake exactly as the SEC served it, with a JSON sidecar describing it,
# and records every decision in two Delta tables. Takes in Candidates (from discover.py); gives back a summary.
#   bronze/raw/edgar/cik=…/accession=…/<doc>.htm       raw bytes, never modified
#   bronze/raw/edgar/cik=…/accession=…/<doc>.htm.json  sidecar: source, dates, content_sha256, size
#   bronze/filings_raw    Delta table, one row per stored file (what the Silver parser reads next)
#   bronze/ingestion_log  Delta table, one row per filing handled: status "landed" or "skipped" (+ skip_reason)
# Raw files sit OUTSIDE the Delta table folders on purpose: Delta's VACUUM deletes files its log doesn't know.
# Write order per company — HTML, sidecar, filings_raw, then ingestion_log last — so a crash mid-run just means the
# next run fetches those filings again and overwrites the same paths; tables merge by accession, so no duplicates.

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import groupby
from typing import Any

from agent_data.domains.edgar.companies import Company
from agent_data.domains.edgar.discover import Candidate, discover_new_filings
from agent_data.domains.edgar.sec_client import SecClient
from agent_data.engine._shared.errors import IngestError
from agent_data.engine._shared.logging import get_logger, log_event
from agent_data.engine.lake.base import LakeStore

_log = get_logger(__name__)

# Where things live in the lake (relative to its root).
RAW_PREFIX = "bronze/raw/edgar"
FILINGS_RAW_TABLE = "bronze/filings_raw"
INGESTION_LOG_TABLE = "bronze/ingestion_log"
SIDECAR_SUFFIX = ".json"
SIDECAR_SCHEMA_VERSION = 1

# Columns shared by both tables: which filing this is.
_FILING_COLUMNS = {
    "accession": "string", "cik": "string", "ticker": "string", "form": "string", "is_amendment": "bool",
    "fiscal_year": "int32", "report_date": "date", "filing_date": "date", "source_url": "string",
}
# One row per stored file; Silver reads this to find documents and check their fingerprints.
FILINGS_RAW_SCHEMA = {
    **_FILING_COLUMNS, "primary_document": "string", "object_path": "string", "sidecar_path": "string",
    "content_sha256": "string", "bytes": "int64", "content_type": "string", "fetched_at": "timestamp",
    "run_id": "string",
}
# One row per filing handled; "landed" rows point at a stored file, "skipped" rows say why there's none.
INGESTION_LOG_SCHEMA = {
    **_FILING_COLUMNS, "status": "string", "skip_reason": "string", "object_path": "string",
    "sidecar_path": "string", "content_sha256": "string", "bytes": "int64", "run_id": "string",
    "logged_at": "timestamp",
}

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class IngestSummary:
    """What one run did; printed as JSON by the runner and returned to the Airflow task."""

    run_id: str
    companies: int
    fiscal_years: list[int]
    already_known: int = 0
    discovered: int = 0
    landed: int = 0
    skipped: int = 0
    bytes_landed: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


def object_path(c: Candidate) -> str:
    # Raw file location, partitioned by company and filing so paths are unique and easy to browse.
    return f"{RAW_PREFIX}/cik={c.cik}/accession={c.accession.replace('-', '')}/{c.primary_document}"


def sidecar_path(raw_path: str) -> str:
    # The sidecar sits right next to its file, with .json appended.
    return raw_path + SIDECAR_SUFFIX


def known_accessions(lake: LakeStore) -> set[str]:
    # Every accession already handled (landed or skipped) — discovery leaves these out.
    return {row["accession"] for row in lake.read_rows(INGESTION_LOG_TABLE, columns=["accession"])}


def ingest(lake: LakeStore, client: SecClient, companies: Sequence[Company], years: Sequence[int], run_id: str,
           now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> IngestSummary:
    # The whole Bronze step: discover new filings, land them company by company, commit each company's rows.
    summary = IngestSummary(run_id=run_id, companies=len(companies), fiscal_years=sorted(years))
    found = discover_new_filings(client, companies, years, known_accessions(lake))
    summary.already_known, summary.discovered = found.already_known, len(found.candidates)
    summary.failures.extend(found.failures)
    for ticker, group in groupby(found.candidates, key=lambda c: c.ticker):
        _land_company(lake, client, list(group), run_id, now, summary)
        log_event(_log, "company_done", ticker=ticker, landed=summary.landed, skipped=summary.skipped)
    return summary


def _land_company(lake: LakeStore, client: SecClient, candidates: list[Candidate], run_id: str,
                  now: Callable[[], datetime], summary: IngestSummary) -> None:
    # Land one company's filings; a single filing failing is recorded and retried next run, the rest still land.
    raw_rows, log_rows = [], []
    for c in candidates:
        try:
            raw_row, log_row = land_filing(lake, client, c, run_id, now())
        except IngestError as exc:
            summary.failures.append({"ticker": c.ticker, "accession": c.accession, "stage": "land",
                                     "error": str(exc)})
            log_event(_log, "land_failed", ticker=c.ticker, accession=c.accession, error=type(exc).__name__)
            continue
        log_rows.append(log_row)
        if raw_row is not None:
            raw_rows.append(raw_row)
            summary.landed += 1
            summary.bytes_landed += raw_row["bytes"]
        else:
            summary.skipped += 1
    # filings_raw first, the log last: a filing only counts as "done" once both are committed.
    lake.upsert_rows(FILINGS_RAW_TABLE, raw_rows, FILINGS_RAW_SCHEMA, key="accession")
    lake.upsert_rows(INGESTION_LOG_TABLE, log_rows, INGESTION_LOG_SCHEMA, key="accession")


def land_filing(lake: LakeStore, client: SecClient, c: Candidate, run_id: str,
                at: datetime) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    # Download and store one filing plus its sidecar; returns (filings_raw row or None if skipped, log row).
    filing = {name: getattr(c, name) for name in _FILING_COLUMNS}
    if c.skip_reason:
        log_event(_log, "filing_skipped", ticker=c.ticker, accession=c.accession, reason=c.skip_reason)
        return None, _log_row(filing, "skipped", c.skip_reason, None, run_id, at)
    doc = client.download(c.source_url)
    raw_path = object_path(c)
    stored = {"object_path": raw_path, "sidecar_path": sidecar_path(raw_path),
              "content_sha256": hashlib.sha256(doc.content).hexdigest(), "bytes": len(doc.content)}
    lake.put_object(raw_path, doc.content)
    lake.put_object(stored["sidecar_path"], _sidecar_json(c, stored, doc.content_type, run_id, at))
    raw_row = {**filing, **stored, "primary_document": c.primary_document, "content_type": doc.content_type,
               "fetched_at": at, "run_id": run_id}
    return raw_row, _log_row(filing, "landed", None, stored, run_id, at)


def _log_row(filing: dict[str, Any], status: str, skip_reason: str | None, stored: dict[str, Any] | None,
             run_id: str, at: datetime) -> dict[str, Any]:
    # One ingestion_log row; the stored-file columns are empty for skipped filings.
    stored = stored or {"object_path": None, "sidecar_path": None, "content_sha256": None, "bytes": None}
    return {**filing, **stored, "status": status, "skip_reason": skip_reason, "run_id": run_id, "logged_at": at}


def _sidecar_json(c: Candidate, stored: dict[str, Any], content_type: str, run_id: str, at: datetime) -> bytes:
    # The sidecar: everything needed to trust and trace the raw file without opening a table.
    payload = {**asdict(c), **stored, "report_date": c.report_date.isoformat(),
               "filing_date": c.filing_date.isoformat(), "content_type": content_type,
               "fetched_at": at.isoformat(), "run_id": run_id, "schema_version": SIDECAR_SCHEMA_VERSION}
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def verify_bronze(lake: LakeStore, deep: bool = False) -> dict[str, Any]:
    # Check the Bronze promises: every file has a sidecar with a valid content_sha256, landed log rows match the
    # stored files exactly, skipped rows have no file, and filings_raw lists the same files. deep=True also
    # re-hashes every file (downloads them all, so it's for occasional audits, not every run).
    paths = lake.list_objects(RAW_PREFIX)
    sidecars = {p[:-len(SIDECAR_SUFFIX)] for p in paths if p.endswith(SIDECAR_SUFFIX)}  # keyed by their file
    files = {p for p in paths if not p.endswith(SIDECAR_SUFFIX)}
    log = lake.read_rows(INGESTION_LOG_TABLE, columns=["accession", "status", "object_path"])
    landed = {r["object_path"] for r in log if r["status"] == "landed"}
    catalogued = {r["object_path"] for r in lake.read_rows(FILINGS_RAW_TABLE, columns=["object_path"])}
    problems = _set_problems(files, sidecars, landed, catalogued)
    problems += [f"skipped row has a file: {r['accession']}" for r in log
                 if r["status"] != "landed" and r["object_path"]]
    problems += _check_sidecars(lake, sorted(files & sidecars), deep)
    return {"ok": not problems, "files": len(files), "sidecars": len(sidecars), "landed_rows": len(landed),
            "skipped_rows": sum(1 for r in log if r["status"] == "skipped"), "filings_raw_rows": len(catalogued),
            "deep": deep, "problem_count": len(problems), "problems": problems[:20]}


def _set_problems(files: set[str], sidecars: set[str], landed: set[str], catalogued: set[str]) -> list[str]:
    # Compare the four views of "what is stored" (files, sidecars, landed log rows, filings_raw) pairwise.
    checks = [("file without sidecar", files - sidecars), ("sidecar without file", sidecars - files),
              ("file not logged as landed", files - landed), ("logged as landed but missing", landed - files),
              ("file not in filings_raw", files - catalogued), ("in filings_raw but missing", catalogued - files)]
    return [f"{label}: {path}" for label, paths in checks for path in sorted(paths)]


def _check_sidecars(lake: LakeStore, files: list[str], deep: bool) -> list[str]:
    # Each sidecar must parse, carry a 64-hex content_sha256 and name its file; deep mode re-hashes the file.
    problems = []
    for path in files:
        try:
            meta = json.loads(lake.get_object(sidecar_path(path)))
        except ValueError:
            problems.append(f"sidecar is not valid JSON: {sidecar_path(path)}")
            continue
        sha = meta.get("content_sha256") if isinstance(meta, dict) else None
        if not isinstance(sha, str) or not _SHA256_HEX.match(sha):
            problems.append(f"sidecar has no valid content_sha256: {sidecar_path(path)}")
        elif meta.get("object_path") != path:
            problems.append(f"sidecar names a different file: {sidecar_path(path)}")
        elif deep and hashlib.sha256(lake.get_object(path)).hexdigest() != sha:
            problems.append(f"file bytes don't match their sidecar hash: {path}")
    return problems
