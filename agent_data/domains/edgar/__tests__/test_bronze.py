# Tests for the Bronze ingest end to end: fake SEC + a real Delta lake in a temp folder. These encode the
# three "done" promises — a rerun fetches zero new filings, every stored file has a sidecar with content_sha256,
# and landed ingestion_log rows equal the stored files — plus skips, partial failures and crash recovery.

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from agent_data.domains.edgar import bronze
from agent_data.domains.edgar.__tests__.fakes import FakeSession, html_for, make_client
from agent_data.domains.edgar.companies import Company
from agent_data.engine._shared.errors import LakeError
from agent_data.engine.lake.delta_store import DeltaLakeStore

pytestmark = pytest.mark.offline

COMPANIES = [Company("AAPL", "IT"), Company("JPM", "Financials")]
YEARS = [2023, 2024, 2025]
FIXED_NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
# In scope across both fakes: AAPL 10-K FY2024, 10-K/A FY2023, 10-K FY2023; JPM FY2025, FY2024, FY2023 (PDF).
EXPECTED_LANDED, EXPECTED_SKIPPED = 5, 1


@pytest.fixture
def lake(tmp_path) -> DeltaLakeStore:
    return DeltaLakeStore(str(tmp_path / "lake"))


def _ingest(lake, session=None, run_id="run-1"):
    # One ingest run against the fake SEC.
    session = session or FakeSession()
    summary = bronze.ingest(lake, make_client(session), COMPANIES, YEARS, run_id, now=lambda: FIXED_NOW)
    return summary, session


def test_first_run_lands_every_in_scope_html_filing(lake):
    summary, session = _ingest(lake)
    assert (summary.discovered, summary.landed, summary.skipped, summary.failures) == (6, 5, 1, [])
    assert len(session.downloads()) == EXPECTED_LANDED          # the PDF was never downloaded
    assert summary.bytes_landed == sum(len(html_for(u.rsplit("/", 1)[1])) for u in session.downloads())


def test_rerun_fetches_zero_new_filings(lake):
    _ingest(lake)
    summary, session = _ingest(lake, run_id="run-2")
    assert (summary.discovered, summary.landed, summary.skipped) == (0, 0, 0)
    assert summary.already_known == EXPECTED_LANDED + EXPECTED_SKIPPED
    assert session.downloads() == []


def test_every_file_has_a_sidecar_with_a_matching_sha256(lake):
    _ingest(lake)
    files = [p for p in lake.list_objects(bronze.RAW_PREFIX) if not p.endswith(".json")]
    assert len(files) == EXPECTED_LANDED
    for path in files:
        meta = json.loads(lake.get_object(bronze.sidecar_path(path)))
        assert meta["content_sha256"] == hashlib.sha256(lake.get_object(path)).hexdigest()
        assert meta["object_path"] == path and meta["schema_version"] == 1
        assert meta["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/")


def test_landed_log_rows_equal_stored_files_and_skips_have_no_file(lake):
    _ingest(lake)
    log = lake.read_rows(bronze.INGESTION_LOG_TABLE)
    landed = [r for r in log if r["status"] == "landed"]
    skipped = [r for r in log if r["status"] == "skipped"]
    files = [p for p in lake.list_objects(bronze.RAW_PREFIX) if not p.endswith(".json")]
    assert sorted(r["object_path"] for r in landed) == sorted(files)
    assert [(r["skip_reason"], r["object_path"]) for r in skipped] == [("non_html", None)]
    assert len(lake.read_rows(bronze.FILINGS_RAW_TABLE)) == len(files)


def test_verify_reports_ok_after_ingest(lake):
    _ingest(lake)
    report = bronze.verify_bronze(lake, deep=True)
    assert report["ok"], report["problems"]
    assert (report["files"], report["sidecars"], report["landed_rows"], report["skipped_rows"]) == (5, 5, 5, 1)


def test_amendment_flag_and_fiscal_year_reach_the_tables(lake):
    _ingest(lake)
    rows = {r["accession"]: r for r in lake.read_rows(bronze.FILINGS_RAW_TABLE)}
    amendment = rows["0000320193-24-000050"]
    assert amendment["is_amendment"] is True and amendment["fiscal_year"] == 2023
    assert rows["0000019617-25-000020"]["fiscal_year"] == 2024     # found on JPM's older filings page


def test_a_failed_download_is_reported_then_retried_next_run(lake):
    session = FakeSession()
    url = next(u for u in session.routes if u.endswith("jpm-20241231.htm"))
    saved = session.routes.pop(url)                                  # SEC returns 404 for this one
    summary, _ = _ingest(lake, session)
    assert summary.landed == EXPECTED_LANDED - 1
    assert [f["accession"] for f in summary.failures] == ["0000019617-25-000020"]
    assert bronze.verify_bronze(lake)["ok"]                          # what did land is consistent
    session.routes[url] = saved                                      # SEC is back
    retry, retry_session = _ingest(lake, session, run_id="run-2")
    assert (retry.landed, retry.failures) == (1, [])
    assert retry_session.downloads()[-1] == url


def test_crash_before_the_log_commit_recovers_without_duplicates(lake, monkeypatch):
    # Simulate the process dying after files + filings_raw were written but before ingestion_log committed.
    real_upsert = lake.upsert_rows

    def crash_on_log(table, rows, schema, key):
        if table == bronze.INGESTION_LOG_TABLE:
            raise LakeError("simulated crash")
        return real_upsert(table, rows, schema, key)

    monkeypatch.setattr(lake, "upsert_rows", crash_on_log)
    with pytest.raises(LakeError, match="simulated crash"):
        _ingest(lake)
    assert not bronze.verify_bronze(lake)["ok"]                      # files exist that the log doesn't know
    monkeypatch.setattr(lake, "upsert_rows", real_upsert)
    summary, _ = _ingest(lake, run_id="run-2")
    report = bronze.verify_bronze(lake, deep=True)
    assert summary.failures == [] and report["ok"], report["problems"]
    assert len(lake.read_rows(bronze.FILINGS_RAW_TABLE)) == EXPECTED_LANDED   # merged, not duplicated


def test_verify_catches_a_missing_sidecar_and_a_tampered_file(lake):
    _ingest(lake)
    files = sorted(p for p in lake.list_objects(bronze.RAW_PREFIX) if not p.endswith(".json"))
    lake.delete_object(bronze.sidecar_path(files[0]))
    lake.put_object(files[1], b"tampered")
    shallow, deep = bronze.verify_bronze(lake), bronze.verify_bronze(lake, deep=True)
    assert any("file without sidecar" in p for p in shallow["problems"])
    assert not any("don't match" in p for p in shallow["problems"])   # shallow mode doesn't re-hash
    assert any("don't match their sidecar hash" in p for p in deep["problems"])


def test_verify_catches_a_sidecar_without_a_valid_hash(lake):
    _ingest(lake)
    path = next(p for p in lake.list_objects(bronze.RAW_PREFIX) if not p.endswith(".json"))
    lake.put_object(bronze.sidecar_path(path), json.dumps({"object_path": path}).encode())
    assert any("no valid content_sha256" in p for p in bronze.verify_bronze(lake)["problems"])


def test_empty_lake_verifies_ok(lake):
    assert bronze.verify_bronze(lake)["ok"]
