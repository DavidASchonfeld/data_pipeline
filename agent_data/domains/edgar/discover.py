# Discovery: works out which filings to fetch. Takes in the company list, target fiscal years and the accession
# numbers already in ingestion_log; gives back the new filings (Candidates) plus any per-company failures.
# Comparing against ingestion_log is what makes a rerun cheap: one filing-list call per company, zero downloads.

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from agent_data.domains.edgar.companies import TARGET_FORMS, Company
from agent_data.domains.edgar.sec_client import FilingRef, SecClient, archive_url
from agent_data.engine._shared.errors import IngestError
from agent_data.engine._shared.logging import get_logger, log_event

_log = get_logger(__name__)

# A document name I am willing to use in a URL and a lake path: letters, digits, dot, dash, underscore only.
_SAFE_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


@dataclass(frozen=True)
class Candidate:
    """A filing in scope that isn't in ingestion_log yet."""

    ticker: str
    cik: str
    accession: str
    form: str
    is_amendment: bool
    fiscal_year: int
    report_date: date
    filing_date: date
    primary_document: str
    source_url: str

    @property
    def skip_reason(self) -> str | None:
        # Why this filing is logged but not downloaded; None means "download it".
        if not self.primary_document:
            return "no_primary_document"
        if not _SAFE_DOCUMENT.match(self.primary_document):
            return "invalid_document_name"
        if not self.primary_document.lower().endswith((".htm", ".html")):
            return "non_html"
        return None


@dataclass
class Discovery:
    """What discovery found: new filings to handle, and companies whose filing list couldn't be read."""

    candidates: list[Candidate] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    already_known: int = 0


def select_filings(refs: Iterable[FilingRef], ticker: str, years: Sequence[int],
                   forms: Sequence[str] = TARGET_FORMS) -> list[Candidate]:
    # Keep target forms whose reporting period ended in a target year; fiscal year = year of report_date.
    wanted_years, wanted_forms = set(years), set(forms)
    picked = []
    for ref in refs:
        if ref.form not in wanted_forms or ref.report_date is None or ref.report_date.year not in wanted_years:
            continue
        picked.append(Candidate(
            ticker=ticker.upper(), cik=ref.cik, accession=ref.accession, form=ref.form,
            is_amendment=ref.form.endswith("/A"), fiscal_year=ref.report_date.year,
            report_date=ref.report_date, filing_date=ref.filing_date, primary_document=ref.primary_document,
            source_url=archive_url(ref.cik, ref.accession, ref.primary_document) if ref.primary_document else "",
        ))
    return sorted(picked, key=lambda c: (c.fiscal_year, c.filing_date, c.accession))


def discover_new_filings(client: SecClient, companies: Sequence[Company], years: Sequence[int],
                         known: set[str]) -> Discovery:
    # For each company: list its filings back to Jan 1 of the earliest target year, keep the in-scope ones,
    # and drop those already logged. One company failing is recorded and the rest still run.
    if not years:
        raise IngestError("At least one fiscal year is required for discovery")
    since = date(min(years), 1, 1)  # a fiscal year's report can't be filed before its period starts
    result = Discovery()
    for company in companies:
        try:
            refs = client.list_filings(client.cik_for(company.ticker), since)
        except IngestError as exc:
            result.failures.append({"ticker": company.ticker, "stage": "discover", "error": str(exc)})
            log_event(_log, "discover_failed", ticker=company.ticker, error=type(exc).__name__)
            continue
        in_scope = select_filings(refs, company.ticker, years)
        new = [c for c in in_scope if c.accession not in known]
        result.already_known += len(in_scope) - len(new)
        result.candidates.extend(new)
        log_event(_log, "discovered", ticker=company.ticker, in_scope=len(in_scope), new=len(new))
    return result
