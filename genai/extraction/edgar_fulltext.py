from __future__ import annotations

import logging
import threading
import time

from genai import config
from genai.extraction import sections

# Fetches a company's full 10-K annual report from SEC EDGAR and returns it split by section.
#
# Self-contained on purpose: the genai/ package must stay plug-and-play (folder-delete to remove,
# runnable standalone), so this re-implements the small EDGAR patterns from airflow/dags/edgar_api.py
# rather than importing them. SEC EDGAR is free public-domain data — no key — but requires a
# descriptive User-Agent and fair-access rate limiting.
#
# WHY deferred imports: requests/bs4/urllib3 are imported inside functions, not at module top, so
# importing this module never fails where bs4 isn't installed (e.g. the dashboard test env) and DAG
# parsing stays memory-light.

logger = logging.getLogger(__name__)

# SEC blocks anonymous callers — identify the script with a contact email (read from config).
EDGAR_USER_AGENT = f"DataPipeline Portfolio Project {config.EDGAR_CONTACT_EMAIL}"

# The three EDGAR endpoints this module uses (all JSON except the final filing document, which is HTML).
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVES_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"


# EdgarError: the single error type every failure in this module raises, so callers catch one
# exception regardless of whether the network, the JSON shape, or the HTML parse was the problem.
class EdgarError(Exception):
    pass


class _RateLimiter:
    """Token-bucket limiter — keeps us under SEC's 10 req/s with a safety margin (default 8/s)."""

    def __init__(self, max_per_second: float = 8.0):
        self._min_interval = 1.0 / max_per_second
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        # Block just long enough that consecutive requests stay under the rate limit.
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last = time.monotonic()


_rate_limiter = _RateLimiter()

# Reused HTTP session (one connection pool, retries configured once); built lazily and thread-safely.
_session = None
_session_lock = threading.Lock()


def _get_session():
    # Build the session on first use so requests/urllib3 stay deferred and config is read once.
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry

                # Bounded retries with backoff on rate-limit/5xx/connection errors — handled once
                # by the adapter, so there is no second hand-rolled retry loop (reference §7).
                retry = Retry(
                    total=config.EDGAR_MAX_RETRIES,
                    backoff_factor=1.0,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(["GET"]),
                    raise_on_status=False,
                )
                session = requests.Session()
                session.mount("https://", HTTPAdapter(max_retries=retry))
                session.headers.update({"User-Agent": EDGAR_USER_AGENT})
                _session = session
    return _session


def _get(url: str):
    # One rate-limited GET with an explicit timeout; any failure becomes an EdgarError.
    _rate_limiter.wait()
    import requests

    try:
        resp = _get_session().get(url, timeout=config.EDGAR_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # Log the URL and error only — never response bodies (no PII).
        logger.error("EDGAR GET failed (%s): %s", url, exc)
        raise EdgarError(f"EDGAR request failed for {url}: {exc}") from exc
    return resp


# CIK cache — company_tickers.json is ~2 MB and rarely changes, so fetch it once per process.
_cik_cache: dict[str, str] | None = None
_cik_lock = threading.Lock()


def resolve_cik(ticker: str) -> str:
    """Convert a ticker (e.g. 'AAPL') to its 10-digit zero-padded SEC CIK string."""
    global _cik_cache
    if _cik_cache is None:
        with _cik_lock:
            if _cik_cache is None:
                resp = _get(_TICKERS_URL)
                try:
                    raw = resp.json()
                    _cik_cache = {
                        entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
                        for entry in raw.values()
                    }
                except (ValueError, KeyError, AttributeError) as exc:
                    raise EdgarError("EDGAR company_tickers.json had an unexpected shape") from exc

    key = ticker.upper()
    if key not in _cik_cache:
        raise EdgarError(f"Ticker {ticker!r} not found in SEC EDGAR company_tickers.json")
    return _cik_cache[key]


def _find_10k(cik: str, year: int) -> dict[str, str]:
    """Locate the 10-K covering a given fiscal year via the Submissions API.

    `year` means the fiscal year the report covers (the period of report), not the year it was
    filed — a 10-K for FY2023 is typically filed in late 2023 or early 2024.
    """
    resp = _get(_SUBMISSIONS_URL.format(cik=cik))
    try:
        data = resp.json()
    except ValueError as exc:
        raise EdgarError(f"EDGAR submissions for CIK {cik} was not valid JSON") from exc

    recent = data.get("filings", {}).get("recent")
    if not recent:
        raise EdgarError(f"No recent filings listed in EDGAR submissions for CIK {cik}")

    # These are parallel arrays in the Submissions API — index i refers to the same filing across all.
    forms = recent.get("form", [])
    report_dates = recent.get("reportDate", [])
    filing_dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    try:
        for i, form in enumerate(forms):
            if form != "10-K":
                continue
            report_date = report_dates[i] if i < len(report_dates) else ""
            if report_date[:4] != str(year):
                continue
            document = primary_docs[i]
            if not document:
                # A 10-K with no primary document (very old filings) can't be fetched as HTML.
                raise EdgarError(f"10-K for CIK {cik} FY{year} has no primary document listed")
            return {
                "accession": accessions[i].replace("-", ""),  # archives path strips the dashes
                "document": document,
                "filing_date": filing_dates[i] if i < len(filing_dates) else report_date,
                "report_date": report_date,
            }
    except (IndexError, AttributeError) as exc:
        raise EdgarError(f"EDGAR submissions for CIK {cik} had misaligned filing arrays") from exc

    raise EdgarError(f"No 10-K found for CIK {cik} covering fiscal year {year}")


def _html_to_text(html: str) -> str:
    # Strip HTML to plain text with BeautifulSoup's stdlib parser (no lxml dependency).
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise EdgarError(
            "beautifulsoup4 is not installed; cannot parse 10-K HTML. "
            "Run: pip install beautifulsoup4 (or check ml-venv was built with GENAI_ENABLED=true)"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    # Drop script/style tags so their code/CSS doesn't leak into the extracted text.
    for tag in soup(["script", "style"]):
        tag.decompose()
    # separator="\n" keeps item headers on their own lines, which the section splitter relies on.
    return soup.get_text(separator="\n")


def fetch_10k_text(ticker: str, year: int) -> dict[str, str]:
    """Fetch a company's 10-K for a fiscal year and return {section_name: text}.

    Sections are Items 1, 1A, 7, 7A; if they can't be cleanly identified the whole filing is
    returned under the key "full". Raises EdgarError on any fetch or parse failure.
    """
    cik = resolve_cik(ticker)
    filing = _find_10k(cik, year)
    # The Archives document path uses the unpadded integer CIK, unlike the zero-padded API CIK.
    doc_url = _ARCHIVES_DOC_URL.format(
        cik_int=int(cik), accession=filing["accession"], document=filing["document"]
    )
    resp = _get(doc_url)
    text = _html_to_text(resp.text)
    if not text.strip():
        raise EdgarError(f"Fetched 10-K for {ticker} FY{year} but extracted no text from {doc_url}")

    logger.info("Fetched 10-K %s FY%s: %d chars from %s", ticker, year, len(text), filing["document"])
    return sections.split_sections(text)
