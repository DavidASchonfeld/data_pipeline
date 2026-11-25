# The SEC EDGAR HTTP client: turns tickers into SEC company IDs (CIKs), lists a company's filings, and downloads
# a filing's document as raw bytes. Takes in plain settings (contact email, timeout, retries, rate); gives back
# FilingRef records and bytes. Every failure becomes one IngestError.
# SEC fair-access rules: a descriptive User-Agent with a contact email, and at most 10 requests/second
# (https://www.sec.gov/os/accessing-edgar-data). I stay under the limit with a shared rate limiter.
# Retries live in the requests retry adapter only (429/5xx with backoff), so there is no second retry loop.

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from agent_data.engine._shared.errors import IngestError, require_extra
from agent_data.engine._shared.logging import get_logger, log_event

_log = get_logger(__name__)

# The EDGAR endpoints I use: the ticker map, a company's filing list (plus older pages), and the archive.
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"

# Older filing pages must look exactly like the SEC's own names, so a bad response can't steer me to another URL.
_PAGE_NAME = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
# An accession number: 10-digit filer ID, 2-digit year, 6-digit sequence.
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")

# The parallel arrays I read from a filing list; index i in each describes the same filing.
_FILING_FIELDS = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")


@dataclass(frozen=True)
class FilingRef:
    """One filing as listed by the SEC; report_date is the end of the period it covers (None if not given)."""

    cik: str                  # 10-digit zero-padded company ID
    accession: str            # e.g. 0000320193-24-000123
    form: str                 # e.g. 10-K, 10-K/A
    filing_date: date
    report_date: date | None
    primary_document: str     # the main document's file name inside the filing, e.g. aapl-20240928.htm


@dataclass(frozen=True)
class Download:
    """A downloaded document: its exact bytes and the content type the server declared."""

    content: bytes
    content_type: str


class RateLimiter:
    """Spaces requests at least 1/rate seconds apart across threads, keeping me under the SEC's limit."""

    def __init__(self, per_second: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if per_second <= 0 or per_second > 10:
            raise IngestError(f"requests_per_second must be in (0, 10] for SEC fair access, got {per_second}")
        self._interval = 1.0 / per_second
        self._clock, self._sleep = clock, sleep
        self._last = float("-inf")
        self._lock = threading.Lock()

    def wait(self) -> None:
        # Sleep just long enough that this request starts at least one interval after the previous one.
        with self._lock:
            gap = self._clock() - self._last
            if gap < self._interval:
                self._sleep(self._interval - gap)
            self._last = self._clock()


class SecClient:
    """Rate-limited, retrying EDGAR client. One instance per run; the ticker map is fetched once and cached."""

    def __init__(self, contact_email: str, *, timeout_seconds: float = 30.0, max_retries: int = 3,
                 requests_per_second: float = 8.0, max_document_bytes: int = 100 * 1024 * 1024,
                 session: Any = None, rate_limiter: RateLimiter | None = None) -> None:
        # Validate the settings the SEC cares about, then build (or accept, for tests) the HTTP session.
        if "@" not in (contact_email or ""):
            raise IngestError("EDGAR needs a contact email in the User-Agent; set EDGAR_CONTACT_EMAIL")
        self.user_agent = f"DataPipeline Portfolio Project {contact_email.strip()}"
        self._timeout = timeout_seconds
        self._max_bytes = max_document_bytes
        self._limiter = rate_limiter or RateLimiter(requests_per_second)
        self._session = session or _build_session(self.user_agent, max_retries)
        self._ciks: dict[str, str] | None = None

    # ── Public calls ────────────────────────────────────────────────────────────────────────────────────────

    def cik_for(self, ticker: str) -> str:
        # Look up a ticker's 10-digit CIK, loading the SEC's ticker map on first use.
        if self._ciks is None:
            self._ciks = _parse_ticker_map(self._get_json(TICKERS_URL))
        cik = self._ciks.get(ticker.strip().upper())
        if cik is None:
            raise IngestError(f"Ticker {ticker!r} is not in the SEC ticker list ({TICKERS_URL})")
        return cik

    def list_filings(self, cik: str, since: date) -> list[FilingRef]:
        # All filings since a date: the "recent" block, plus any older pages that reach back to that date.
        data = self._get_json(SUBMISSIONS_URL.format(cik=cik))
        filings = data.get("filings") if isinstance(data, Mapping) else None
        if not isinstance(filings, Mapping) or not isinstance(filings.get("recent"), Mapping):
            raise IngestError(f"EDGAR submissions for CIK {cik} has no filings.recent block")
        refs = _parse_filing_block(filings["recent"], cik)
        for name in _older_pages_needed(filings.get("files"), since, cik):
            refs.extend(_parse_filing_block(self._get_json(SUBMISSIONS_PAGE_URL.format(name=name)), cik))
        return [ref for ref in refs if ref.filing_date >= since]

    def download(self, url: str) -> Download:
        # Stream a document into memory with a size cap, so one huge or endless response can't exhaust RAM.
        requests = require_extra("requests", "edgar")
        resp = self._request(url, stream=True)
        try:
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise IngestError(f"{url} is {int(declared):,} bytes, over the {self._max_bytes:,}-byte cap")
            content = self._read_capped(resp, url)
        except requests.exceptions.RequestException as exc:
            raise IngestError(f"EDGAR download interrupted for {url}: {exc}") from exc
        finally:
            resp.close()
        if not content:
            raise IngestError(f"EDGAR returned an empty document for {url}")
        return Download(content=content, content_type=resp.headers.get("Content-Type", ""))

    # ── Internals ───────────────────────────────────────────────────────────────────────────────────────────

    def _get_json(self, url: str) -> Any:
        # GET a JSON endpoint; a body that isn't JSON is an IngestError, never a raw ValueError.
        resp = self._request(url)
        try:
            return resp.json()
        except ValueError as exc:
            raise IngestError(f"EDGAR response from {url} was not valid JSON") from exc

    def _request(self, url: str, stream: bool = False) -> Any:
        # One rate-limited GET with an explicit timeout; transport and HTTP errors become IngestError.
        requests = require_extra("requests", "edgar")
        self._limiter.wait()
        try:
            resp = self._session.get(url, timeout=self._timeout, stream=stream)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            # Log the URL and error type only — never response bodies.
            log_event(_log, "edgar_request_failed", url=url, error=type(exc).__name__)
            raise IngestError(f"EDGAR request failed for {url}: {exc}") from exc
        return resp

    def _read_capped(self, resp: Any, url: str) -> bytes:
        # Read the body in 1 MiB pieces and stop as soon as it passes the cap.
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            total += len(chunk)
            if total > self._max_bytes:
                raise IngestError(f"{url} grew past the {self._max_bytes:,}-byte cap while downloading")
            chunks.append(chunk)
        return b"".join(chunks)


def archive_url(cik: str, accession: str, document: str) -> str:
    # The public URL of a filing's document; the archive uses the unpadded CIK and the accession without dashes.
    return ARCHIVES_URL.format(cik_int=int(cik), accession_nodash=accession.replace("-", ""), document=document)


def _build_session(user_agent: str, max_retries: int) -> Any:
    # A pooled session that identifies me to the SEC and retries rate-limit/server errors with backoff.
    requests = require_extra("requests", "edgar")
    adapters = require_extra("requests.adapters", "edgar")
    retry_mod = require_extra("urllib3.util.retry", "edgar")
    retry = retry_mod.Retry(total=max_retries, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                            allowed_methods=frozenset(["GET"]), respect_retry_after_header=True,
                            raise_on_status=False)
    session = requests.Session()
    session.mount("https://", adapters.HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return session


def _parse_ticker_map(raw: Any) -> dict[str, str]:
    # company_tickers.json is {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}; build ticker -> padded CIK.
    if not isinstance(raw, Mapping) or not raw:
        raise IngestError("EDGAR company_tickers.json was empty or not an object")
    try:
        return {str(e["ticker"]).upper(): str(int(e["cik_str"])).zfill(10) for e in raw.values()}
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestError("EDGAR company_tickers.json had an unexpected shape") from exc


def _older_pages_needed(files: Any, since: date, cik: str) -> list[str]:
    # Pick the older filing pages whose newest filing is on or after `since`; skip the rest (saves requests).
    if files is None:
        return []
    if not isinstance(files, list):
        raise IngestError(f"EDGAR submissions for CIK {cik} has a malformed filings.files list")
    names = []
    for page in files:
        name, newest = (page.get("name"), page.get("filingTo")) if isinstance(page, Mapping) else (None, None)
        if not isinstance(name, str) or not _PAGE_NAME.match(name):
            raise IngestError(f"EDGAR submissions for CIK {cik} lists an unexpected older page {name!r}")
        if _parse_date(newest) is None or _parse_date(newest) >= since:  # unknown range: fetch to be safe
            names.append(name)
    return names


def _parse_filing_block(block: Any, cik: str) -> list[FilingRef]:
    # Turn the SEC's parallel arrays into FilingRef records, refusing misaligned or malformed data.
    if not isinstance(block, Mapping):
        raise IngestError(f"EDGAR filing list for CIK {cik} is not an object")
    columns = [block.get(field, []) for field in _FILING_FIELDS]
    if not all(isinstance(col, list) for col in columns) or len({len(col) for col in columns}) > 1:
        raise IngestError(f"EDGAR filing list for CIK {cik} has missing or misaligned arrays")
    refs = []
    for accession, form, filed, reported, document in zip(*columns, strict=True):  # lengths already checked equal above
        filing_date = _parse_date(filed)
        if not isinstance(accession, str) or not _ACCESSION.match(accession) or filing_date is None:
            raise IngestError(f"EDGAR filing list for CIK {cik} has a malformed entry ({accession!r}, {filed!r})")
        refs.append(FilingRef(cik=cik, accession=accession, form=str(form), filing_date=filing_date,
                              report_date=_parse_date(reported), primary_document=str(document or "")))
    return refs


def _parse_date(value: Any) -> date | None:
    # "2024-09-28" -> date; blanks and junk -> None (the SEC leaves reportDate empty on some forms).
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
