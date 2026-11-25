# A fake SEC for offline tests: canned JSON and HTML served through an object that behaves like a requests
# Session, so the real SecClient code (parsing, caps, errors) runs without any network.

from __future__ import annotations

import json
from typing import Any

import requests

from agent_data.domains.edgar.sec_client import RateLimiter, SecClient

APPLE_CIK, JPM_CIK = "0000320193", "0000019617"
TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
           "1": {"cik_str": 19617, "ticker": "JPM", "title": "JPMorgan"}}


def block(*filings: tuple[str, str, str, str, str]) -> dict[str, list[str]]:
    # Build the SEC's parallel-array filing block from (accession, form, filed, reported, document) tuples.
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    return {k: [f[i] for f in filings] for i, k in enumerate(keys)}


# Apple: everything in the "recent" block — two in-scope 10-Ks, one amendment, plus out-of-scope noise.
APPLE = {"filings": {"recent": block(
    ("0000320193-24-000123", "10-K", "2024-11-01", "2024-09-28", "aapl-20240928.htm"),
    ("0000320193-24-000100", "10-Q", "2024-08-02", "2024-06-29", "aapl-20240629.htm"),
    ("0000320193-24-000050", "10-K/A", "2024-02-01", "2023-09-30", "aapl-20230930a.htm"),
    ("0000320193-23-000106", "10-K", "2023-11-03", "2023-09-30", "aapl-20230930.htm"),
    ("0000320193-22-000108", "10-K", "2022-10-28", "2022-09-24", "aapl-20220924.htm"),
), "files": []}}

# JPM: a busy filer — only FY2025 is "recent"; FY2024 and FY2023 sit on an older page (FY2023 is a PDF).
JPM = {"filings": {"recent": block(
    ("0000019617-26-000010", "10-K", "2026-02-15", "2025-12-31", "jpm-20251231.htm"),
    ("0000019617-26-000500", "424B2", "2026-03-01", "", "prospectus.htm"),
), "files": [
    {"name": "CIK0000019617-submissions-001.json", "filingFrom": "2023-01-01", "filingTo": "2025-12-31"},
    {"name": "CIK0000019617-submissions-002.json", "filingFrom": "2015-01-01", "filingTo": "2019-12-31"},
]}}
JPM_PAGE_1 = block(
    ("0000019617-25-000020", "10-K", "2025-02-14", "2024-12-31", "jpm-20241231.htm"),
    ("0000019617-24-000020", "10-K", "2024-02-16", "2023-12-31", "jpm-20231231.pdf"),
)


def html_for(doc: str) -> bytes:
    # Deterministic fake filing HTML per document name.
    return f"<html><body>10-K body for {doc}</body></html>".encode()


class FakeResponse:
    """Just enough of requests.Response for SecClient."""

    def __init__(self, url: str, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.url, self.status_code, self._body = url, status, body
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")

    def json(self) -> Any:
        return json.loads(self._body)

    def iter_content(self, chunk_size: int) -> Any:
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Serves canned responses by URL and records every request; unknown URLs are 404s."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = routes if routes is not None else default_routes()
        self.calls: list[str] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float, stream: bool = False) -> FakeResponse:
        assert timeout > 0  # every call must carry an explicit timeout
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            return FakeResponse(url, 404, b"not found")
        if isinstance(route, FakeResponse):
            return route
        body = route if isinstance(route, bytes) else json.dumps(route).encode()
        return FakeResponse(url, 200, body, {"Content-Type": "text/html" if isinstance(route, bytes) else
                                             "application/json"})

    def downloads(self) -> list[str]:
        # Only the archive (document) requests, i.e. actual filing downloads.
        return [u for u in self.calls if "/Archives/" in u]


def default_routes() -> dict[str, Any]:
    # The SEC as seen by the tests: ticker map, two companies' filing lists, and every in-scope document.
    routes: dict[str, Any] = {
        "https://www.sec.gov/files/company_tickers.json": TICKERS,
        f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json": APPLE,
        f"https://data.sec.gov/submissions/CIK{JPM_CIK}.json": JPM,
        "https://data.sec.gov/submissions/CIK0000019617-submissions-001.json": JPM_PAGE_1,
    }
    docs = [("320193", "000032019324000123", "aapl-20240928.htm"),
            ("320193", "000032019324000050", "aapl-20230930a.htm"),
            ("320193", "000032019323000106", "aapl-20230930.htm"),
            ("19617", "000001961726000010", "jpm-20251231.htm"),
            ("19617", "000001961725000020", "jpm-20241231.htm")]
    for cik, acc, doc in docs:
        routes[f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"] = html_for(doc)
    return routes


def make_client(session: FakeSession | None = None, **kwargs: Any) -> SecClient:
    # A real SecClient wired to the fake session, with a rate limiter that never sleeps.
    limiter = RateLimiter(8.0, sleep=lambda _seconds: None)
    return SecClient("tests@example.com", session=session or FakeSession(), rate_limiter=limiter, **kwargs)
