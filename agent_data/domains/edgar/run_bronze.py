# Command-line runner for the Bronze ingest. The Airflow DAG runs it as a subprocess under /opt/ml-venv (so
# delta-rs and requests never load inside the scheduler process); it also runs on a laptop for the local profile.
#   python -m agent_data.domains.edgar.run_bronze                       # all companies, default fiscal years
#   python -m agent_data.domains.edgar.run_bronze --tickers AAPL --years 2024
#   python -m agent_data.domains.edgar.run_bronze --dry-run             # list what would be fetched; no writes
#   python -m agent_data.domains.edgar.run_bronze --verify-only --deep  # audit Bronze without calling the SEC
# Logs go to stderr; the LAST stdout line is one JSON summary the DAG parses. Exit 0 = clean, 1 = something failed.

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from agent_data import platform
from agent_data.config import AgentDataSettings, load_settings
from agent_data.domains.edgar.bronze import ingest, known_accessions, verify_bronze
from agent_data.domains.edgar.companies import COMPANIES, DEFAULT_FISCAL_YEARS, Company
from agent_data.domains.edgar.discover import discover_new_filings
from agent_data.domains.edgar.sec_client import SecClient
from agent_data.engine._shared.errors import AgentDataError, IngestError
from agent_data.engine.lake.base import LakeStore


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    # The runner's options; defaults cover the scheduled weekly run.
    parser = argparse.ArgumentParser(description="Land new SEC 10-K filings in the Bronze lake layer.")
    parser.add_argument("--tickers", nargs="+", help="only these tickers (default: the full company list)")
    parser.add_argument("--years", nargs="+", type=int, help="fiscal years (default: %(default)s)",
                        default=list(DEFAULT_FISCAL_YEARS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="discover only: no downloads, no lake writes")
    mode.add_argument("--verify-only", action="store_true", help="check Bronze consistency without calling the SEC")
    parser.add_argument("--deep", action="store_true", help="when verifying, re-hash every stored file")
    return parser.parse_args(argv)


def pick_companies(tickers: Sequence[str] | None) -> list[Company]:
    # The tracked list, or just the named tickers (a ticker outside the list is allowed for one-off local runs).
    if not tickers:
        return list(COMPANIES)
    by_ticker = {c.ticker: c for c in COMPANIES}
    unique = dict.fromkeys(t.strip().upper() for t in tickers)   # upper-case first so "aapl" and "AAPL" merge
    return [by_ticker.get(t, Company(t, "unlisted")) for t in unique]


def build_client(settings: AgentDataSettings) -> SecClient:
    # Map the EDGAR_* settings onto the client (the domain code itself never reads env vars).
    e = settings.edgar
    return SecClient(e.contact_email, timeout_seconds=e.timeout_seconds, max_retries=e.max_retries,
                     requests_per_second=e.requests_per_second, max_document_bytes=e.max_document_mb * 1024 * 1024)


def run(args: argparse.Namespace, lake: LakeStore, client: SecClient | None) -> dict[str, Any]:
    # Do the requested mode and return the summary dict (the caller prints it and picks the exit code).
    if args.verify_only:
        report = verify_bronze(lake, deep=args.deep)
        return {"mode": "verify", "ok": report["ok"], "verify": report}
    if client is None:  # only verify mode runs without an SEC client
        raise IngestError("An SEC client is required to discover or ingest filings")
    companies = pick_companies(args.tickers)
    if args.dry_run:
        found = discover_new_filings(client, companies, args.years, known_accessions(lake))
        return {"mode": "dry_run", "ok": not found.failures, "already_known": found.already_known,
                "would_fetch": [f"{c.ticker} FY{c.fiscal_year} {c.form} {c.accession}" for c in found.candidates],
                "failures": found.failures}
    lake.check_writable()   # fail fast with an access hint before any SEC traffic
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    summary = ingest(lake, client, companies, args.years, run_id)
    report = verify_bronze(lake, deep=args.deep)
    return {"mode": "ingest", "ok": not summary.failures and report["ok"], **asdict(summary), "verify": report}


def main(argv: Sequence[str] | None = None, *, lake: LakeStore | None = None,
         client: SecClient | None = None) -> int:
    # Entry point: set up stderr logging, build the lake and client from settings, run, print the JSON summary.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    try:
        settings = load_settings() if lake is None or (client is None and not args.verify_only) else None
        lake = lake or platform.get_lake(settings)
        client = client or (None if args.verify_only else build_client(settings))
        result = run(args, lake, client)
    except AgentDataError as exc:
        # Known failure (config, lake, SEC): report it in the same JSON shape so the DAG can always parse it.
        result = {"mode": "error", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, default=str, sort_keys=True))
    return 0 if result["ok"] else 1


def exit_now(code: int) -> None:
    # Flush every output, then end the process at once. All lake writes are already committed by now; skipping
    # native-library teardown avoids a rare hang in pyarrow's thread-pool shutdown at interpreter exit, which
    # would otherwise leave the Airflow task stuck until its timeout.
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    exit_now(main())
