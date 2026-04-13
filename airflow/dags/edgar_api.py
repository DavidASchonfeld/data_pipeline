# Python Libraries
import json
import os
import time
import threading
from typing import Any

import requests


# ── SEC EDGAR API info ───────────────────────────────────────────────────────
# No API key required — SEC EDGAR is free U.S. government public domain data
# Rate limit: 10 requests/second (SEC policy, not a technical block)
# Required: User-Agent header with contact info (SEC will block without it)
# Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
# ─────────────────────────────────────────────────────────────────────────────

# Contact email for SEC User-Agent — loaded from env so it stays out of git history
EDGAR_CONTACT_EMAIL = os.environ.get("EDGAR_CONTACT_EMAIL", "contact@stocklivedata.dev")

# SEC requires a descriptive User-Agent so they can contact you if your script misbehaves
EDGAR_USER_AGENT = f"DataPipeline Portfolio Project {EDGAR_CONTACT_EMAIL}"

# Base URLs for the two SEC EDGAR endpoints we use
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


# ── Rate Limiter ─────────────────────────────────────────────────────────────
# SEC EDGAR allows 10 requests/second. Even though our pipeline only makes a
# handful of calls, building a proper rate limiter is best practice and shows
# recruiters you think about API etiquette and production-readiness.
#
# This is a token-bucket rate limiter: it allows bursts up to max_requests,
# then enforces a minimum delay between subsequent calls.
# ─────────────────────────────────────────────────────────────────────────────
class RateLimiter:
    """Token-bucket rate limiter — controls how fast we hit external APIs."""

    def __init__(self, max_requests_per_second: float = 8.0):
        # Stay under SEC's 10/sec limit with a safety margin of 2 req/sec
        self.min_interval: float = 1.0 / max_requests_per_second
        # Tracks when we last made a request so we know how long to wait
        self._last_request_time: float = 0.0
        # Thread lock prevents race conditions if multiple tasks share this limiter
        self._lock: threading.Lock = threading.Lock()

    def wait(self) -> None:
        """Block until enough time has passed since the last request."""
        with self._lock:
            now = time.monotonic()
            # Calculate how long we need to sleep before the next request is allowed
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                # Sleep just long enough to stay under the rate limit
                time.sleep(self.min_interval - elapsed)
            # Record this request's timestamp for the next call's calculation
            self._last_request_time = time.monotonic()


# Module-level rate limiter — shared across all functions in this file
_rate_limiter = RateLimiter(max_requests_per_second=8.0)


def _get_with_rate_limit(url: str) -> requests.Response:
    """Make a GET request to SEC EDGAR, respecting rate limits and required headers."""
    # Pause if we're sending requests too fast (SEC policy: max 10/sec)
    _rate_limiter.wait()

    # SEC blocks requests without a User-Agent identifying the caller
    headers = {"User-Agent": EDGAR_USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        # Raise an exception for 4xx/5xx HTTP status codes
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        print(f"SEC EDGAR HTTP error: {response.status_code} for {url}")
        raise
    except Exception as error:
        print(f"SEC EDGAR request failed: {error}")
        raise

    return response


# ── CIK cache ────────────────────────────────────────────────────────────────
# company_tickers.json is ~2MB and rarely changes. We fetch it once per DAG
# run and cache it in memory so we don't re-download it for every ticker.
# ─────────────────────────────────────────────────────────────────────────────
_cik_cache: dict[str, str] | None = None


def resolve_cik(ticker: str) -> str:
    """
    Convert a ticker symbol (e.g. 'AAPL') to a 10-digit zero-padded CIK string.

    SEC EDGAR identifies companies by CIK, not ticker. This function downloads
    the official SEC ticker→CIK mapping and caches it for the duration of the
    DAG run so subsequent lookups are instant.
    """
    global _cik_cache

    if _cik_cache is None:
        # Fetch the official SEC ticker-to-CIK mapping (one HTTP call, cached after)
        response = _get_with_rate_limit(EDGAR_TICKERS_URL)
        raw_mapping: dict = json.loads(response.content)
        # Build a fast lookup dict: {"AAPL": "0000320193", "MSFT": "0000789019", ...}
        _cik_cache = {
            entry["ticker"]: str(entry["cik_str"]).zfill(10)
            for entry in raw_mapping.values()
        }

    # Convert ticker to uppercase for case-insensitive matching
    ticker_upper = ticker.upper()

    if ticker_upper not in _cik_cache:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR company_tickers.json")

    return _cik_cache[ticker_upper]


def fetch_company_facts(cik: str) -> dict[str, Any]:
    """
    Fetch all XBRL financial data for a company from SEC EDGAR.

    Parameters
    ----------
    cik : 10-digit zero-padded CIK string (from resolve_cik())

    Returns
    -------
    Raw JSON response as a dict. Top-level shape:
        {
          "cik": 320193,
          "entityName": "Apple Inc.",
          "facts": {
              "us-gaap": {
                  "NetIncomeLoss": { "units": { "USD": [...] }, ... },
                  ...
              }
          }
        }
    """
    # Build the URL with zero-padded CIK (SEC requires exactly 10 digits)
    url = EDGAR_COMPANY_FACTS_URL.format(cik=cik)

    response = _get_with_rate_limit(url)
    data: dict = json.loads(response.content)

    # Validate that the response contains the expected XBRL structure
    if "facts" not in data:
        raise ValueError(f"Unexpected response shape for CIK {cik}: missing 'facts' key")

    if "us-gaap" not in data["facts"]:
        raise ValueError(f"No US-GAAP data found for CIK {cik} — company may use IFRS or be non-US")

    return data
