# Tests for the SEC client against the fake SEC: ticker lookup, filing lists (including older pages), the
# download size cap, rate limiting, and that every failure surfaces as one IngestError.

from __future__ import annotations

from datetime import date

import pytest

from agent_data.domains.edgar.__tests__.fakes import APPLE_CIK, JPM_CIK, FakeResponse, FakeSession, make_client
from agent_data.domains.edgar.sec_client import RateLimiter, SecClient, _build_session, archive_url
from agent_data.engine._shared.errors import IngestError

pytestmark = pytest.mark.offline


def test_user_agent_identifies_me_with_a_contact_email():
    assert make_client().user_agent == "DataPipeline Portfolio Project tests@example.com"
    with pytest.raises(IngestError, match="EDGAR_CONTACT_EMAIL"):
        SecClient("", session=FakeSession())


def test_real_session_sends_user_agent_and_retries_rate_limits():
    session = _build_session("UA test@example.com", max_retries=3)
    assert session.headers["User-Agent"] == "UA test@example.com"
    retry = session.get_adapter("https://www.sec.gov").max_retries
    assert retry.total == 3 and 429 in retry.status_forcelist and retry.respect_retry_after_header


def test_ticker_map_is_fetched_once_and_cached():
    session = FakeSession()
    client = make_client(session)
    assert client.cik_for("aapl") == APPLE_CIK
    assert client.cik_for("JPM") == JPM_CIK
    assert session.calls.count("https://www.sec.gov/files/company_tickers.json") == 1


def test_unknown_ticker_is_an_ingest_error():
    with pytest.raises(IngestError, match="ZZZZ"):
        make_client().cik_for("ZZZZ")


def test_recent_filings_are_parsed_and_filtered_by_date():
    refs = make_client().list_filings(APPLE_CIK, since=date(2023, 1, 1))
    assert {r.accession for r in refs} == {"0000320193-24-000123", "0000320193-24-000100",
                                           "0000320193-24-000050", "0000320193-23-000106"}
    amendment = next(r for r in refs if r.form == "10-K/A")
    assert amendment.report_date == date(2023, 9, 30)


def test_older_pages_are_fetched_only_when_they_reach_the_window():
    session = FakeSession()
    refs = make_client(session).list_filings(JPM_CIK, since=date(2023, 1, 1))
    assert "https://data.sec.gov/submissions/CIK0000019617-submissions-001.json" in session.calls
    assert "https://data.sec.gov/submissions/CIK0000019617-submissions-002.json" not in session.calls
    assert {r.report_date for r in refs if r.form == "10-K"} == {date(2025, 12, 31), date(2024, 12, 31),
                                                                 date(2023, 12, 31)}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"nope": 1}, "no filings.recent"),
        ({"filings": {"recent": {"accessionNumber": ["0000320193-24-000123"], "form": []}}}, "misaligned"),
        ({"filings": {"recent": {"accessionNumber": ["bad"], "form": ["10-K"], "filingDate": ["2024-01-01"],
                                 "reportDate": [""], "primaryDocument": ["a.htm"]}}}, "malformed entry"),
        ({"filings": {"recent": {}, "files": [{"name": "../../evil.json", "filingTo": "2025-01-01"}]}},
         "unexpected older page"),
        ({"filings": {"recent": {}, "files": "oops"}}, "malformed filings.files"),
    ],
)
def test_malformed_filing_lists_are_rejected(payload, message):
    session = FakeSession({f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json": payload})
    with pytest.raises(IngestError, match=message):
        make_client(session).list_filings(APPLE_CIK, since=date(2023, 1, 1))


def test_http_errors_and_bad_json_become_ingest_errors():
    url = "https://www.sec.gov/files/company_tickers.json"
    with pytest.raises(IngestError, match="request failed"):
        make_client(FakeSession({})).cik_for("AAPL")
    with pytest.raises(IngestError, match="not valid JSON"):
        make_client(FakeSession({url: FakeResponse(url, 200, b"<html>")})).cik_for("AAPL")


def test_download_returns_exact_bytes_and_content_type():
    url = archive_url(APPLE_CIK, "0000320193-24-000123", "aapl-20240928.htm")
    doc = make_client().download(url)
    assert doc.content.startswith(b"<html>") and doc.content_type == "text/html"


def test_download_cap_uses_declared_size_and_streamed_size():
    url = "https://www.sec.gov/Archives/edgar/data/1/1/big.htm"
    declared = FakeResponse(url, 200, b"x", {"Content-Length": "5000"})
    with pytest.raises(IngestError, match="over the"):
        make_client(FakeSession({url: declared}), max_document_bytes=1000).download(url)
    streamed = FakeResponse(url, 200, b"x" * 5000)
    with pytest.raises(IngestError, match="grew past"):
        make_client(FakeSession({url: streamed}), max_document_bytes=1000).download(url)
    assert streamed.closed  # the connection is always released


def test_empty_document_is_an_error():
    url = "https://www.sec.gov/Archives/edgar/data/1/1/empty.htm"
    with pytest.raises(IngestError, match="empty document"):
        make_client(FakeSession({url: FakeResponse(url, 200, b"")})).download(url)


def test_rate_limiter_spaces_requests():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    limiter = RateLimiter(4.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()
    limiter.wait()
    assert slept == [0.25]


@pytest.mark.parametrize("rate", [0, 11])
def test_rate_limiter_refuses_rates_outside_sec_fair_access(rate):
    with pytest.raises(IngestError, match="fair access"):
        RateLimiter(rate)


def test_archive_url_uses_unpadded_cik_and_undashed_accession():
    assert archive_url(APPLE_CIK, "0000320193-24-000123", "a.htm") == \
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a.htm"
