# Tests for the EDGAR 10-K fetcher. The offline tests stub the network via _get (no requests/SDK
# calls); one @pytest.mark.live test does a real fetch and is excluded from CI and deploys.
import sys
from pathlib import Path

import pytest

from genai.extraction.__tests__.conftest import FakeResp

_FIXTURE = Path(__file__).parent / "fixtures" / "aapl_10k_sample.html"

# Minimal Submissions API shape — parallel arrays, two 10-Ks for different fiscal years.
_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-Q", "10-K", "10-K"],
            "reportDate": ["2023-06-30", "2023-09-30", "2022-09-30"],
            "filingDate": ["2023-07-15", "2023-11-01", "2022-11-01"],
            "accessionNumber": ["0000320193-23-000077", "0000320193-23-000106", "0000320193-22-000108"],
            "primaryDocument": ["aapl-q3.htm", "aapl-20230930.htm", "aapl-20220930.htm"],
        }
    }
}

_TICKERS = {"0": {"ticker": "AAPL", "cik_str": 320193}}


def _dispatch(html=""):
    # Route each URL to the right canned response, mimicking the three EDGAR endpoints.
    def _get(url):
        if "company_tickers" in url:
            return FakeResp(json_data=_TICKERS)
        if "submissions" in url:
            return FakeResp(json_data=_SUBMISSIONS)
        return FakeResp(text=html)
    return _get


def test_resolve_cik_zero_pads(edgar, monkeypatch):
    monkeypatch.setattr(edgar, "_get", _dispatch())
    assert edgar.resolve_cik("aapl") == "0000320193"


def test_resolve_cik_unknown_ticker_raises(edgar, monkeypatch):
    monkeypatch.setattr(edgar, "_get", _dispatch())
    with pytest.raises(edgar.EdgarError):
        edgar.resolve_cik("NOPE")


def test_find_10k_matches_fiscal_year(edgar, monkeypatch):
    monkeypatch.setattr(edgar, "_get", _dispatch())
    filing = edgar._find_10k("0000320193", 2023)
    assert filing["accession"] == "000032019323000106"  # dashes stripped for the archives path
    assert filing["document"] == "aapl-20230930.htm"
    assert filing["report_date"] == "2023-09-30"


def test_find_10k_missing_year_raises(edgar, monkeypatch):
    monkeypatch.setattr(edgar, "_get", _dispatch())
    with pytest.raises(edgar.EdgarError):
        edgar._find_10k("0000320193", 1999)


def test_find_10k_no_filings_raises(edgar, monkeypatch):
    monkeypatch.setattr(edgar, "_get", lambda url: FakeResp(json_data={"filings": {}}))
    with pytest.raises(edgar.EdgarError):
        edgar._find_10k("0000320193", 2023)


def test_fetch_builds_unpadded_archives_url(edgar, monkeypatch):
    pytest.importorskip("bs4")
    seen = []
    base = _dispatch(_FIXTURE.read_text())

    def spy(url):
        seen.append(url)
        return base(url)

    monkeypatch.setattr(edgar, "_get", spy)
    edgar.fetch_10k_text("AAPL", 2023)
    # Archives path uses the unpadded CIK and the dash-stripped accession number.
    assert any(
        "/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm" in u for u in seen
    )


def test_fetch_10k_text_end_to_end(edgar, monkeypatch):
    pytest.importorskip("bs4")
    monkeypatch.setattr(edgar, "_get", _dispatch(_FIXTURE.read_text()))
    result = edgar.fetch_10k_text("AAPL", 2023)
    assert "Item 1A - Risk Factors" in result
    assert "Item 7 - Management Discussion and Analysis" in result
    assert all(len(v) > 0 for v in result.values())
    # Script/style contents must not leak into the extracted text.
    assert "scripts and styles must not leak" not in " ".join(result.values())


def test_fetch_empty_html_raises(edgar, monkeypatch):
    pytest.importorskip("bs4")
    monkeypatch.setattr(edgar, "_get", _dispatch("<html><body></body></html>"))
    with pytest.raises(edgar.EdgarError):
        edgar.fetch_10k_text("AAPL", 2023)


def test_html_to_text_without_bs4_raises(edgar, monkeypatch):
    # Setting bs4 to None in sys.modules makes the import raise ImportError, which must surface as EdgarError.
    monkeypatch.setitem(sys.modules, "bs4", None)
    with pytest.raises(edgar.EdgarError):
        edgar._html_to_text("<html><body><p>hi</p></body></html>")


@pytest.mark.live
def test_fetch_10k_text_live():
    # Real network call to SEC EDGAR — run only with `pytest -m live`, never in CI or deploy.
    from genai.extraction.edgar_fulltext import fetch_10k_text

    result = fetch_10k_text("AAPL", 2023)
    assert result
    assert all(len(v) > 0 for v in result.values())
