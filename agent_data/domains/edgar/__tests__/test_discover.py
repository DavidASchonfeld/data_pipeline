# Tests for discovery: which filings are in scope (form, fiscal year), how amendments and odd documents are
# flagged, that already-logged filings are left out, and that one company failing doesn't stop the others.

from __future__ import annotations

from datetime import date

import pytest

from agent_data.domains.edgar.__tests__.fakes import APPLE_CIK, FakeSession, make_client
from agent_data.domains.edgar.companies import COMPANIES, DEFAULT_FISCAL_YEARS, Company
from agent_data.domains.edgar.discover import discover_new_filings, select_filings
from agent_data.domains.edgar.sec_client import FilingRef
from agent_data.engine._shared.errors import IngestError

pytestmark = pytest.mark.offline


def _ref(accession: str, form: str = "10-K", reported: str | None = "2024-09-28", doc: str = "a.htm") -> FilingRef:
    # A filing reference with sensible defaults.
    return FilingRef(cik=APPLE_CIK, accession=accession, form=form, filing_date=date(2024, 11, 1),
                     report_date=date.fromisoformat(reported) if reported else None, primary_document=doc)


def test_company_list_covers_25_companies_and_11_sectors():
    assert len(COMPANIES) == 25
    assert len({c.ticker for c in COMPANIES}) == 25
    assert len({c.sector for c in COMPANIES}) == 11
    assert {"AAPL", "MSFT", "GOOGL"} <= {c.ticker for c in COMPANIES}   # the evaluation set's companies
    assert DEFAULT_FISCAL_YEARS == (2023, 2024, 2025)


def test_select_keeps_target_forms_in_target_years_only():
    refs = [_ref("0000320193-24-000001"),                                   # 10-K FY2024: keep
            _ref("0000320193-24-000002", form="10-Q"),                      # wrong form
            _ref("0000320193-24-000003", reported="2022-09-24"),            # wrong year
            _ref("0000320193-24-000004", reported=None),                    # no period: can't place it
            _ref("0000320193-24-000005", form="10-K/A", reported="2023-09-30")]
    picked = select_filings(refs, "aapl", [2023, 2024])
    assert [(c.accession, c.fiscal_year, c.is_amendment) for c in picked] == [
        ("0000320193-24-000005", 2023, True), ("0000320193-24-000001", 2024, False)]
    assert picked[0].ticker == "AAPL"


@pytest.mark.parametrize(
    ("doc", "reason"),
    [("a.htm", None), ("A.HTML", None), ("a.pdf", "non_html"), ("a.txt", "non_html"), ("", "no_primary_document"),
     ("../x.htm", "invalid_document_name"), ("dir/x.htm", "invalid_document_name")],
)
def test_skip_reasons(doc, reason):
    (candidate,) = select_filings([_ref("0000320193-24-000001", doc=doc)], "AAPL", [2024])
    assert candidate.skip_reason == reason


def test_discovery_leaves_out_known_filings_and_counts_them():
    client = make_client()
    found = discover_new_filings(client, [Company("AAPL", "IT")], [2023, 2024], known={"0000320193-24-000123"})
    assert {c.accession for c in found.candidates} == {"0000320193-24-000050", "0000320193-23-000106"}
    assert found.already_known == 1 and found.failures == []


def test_one_company_failing_is_recorded_and_others_continue():
    found = discover_new_filings(make_client(), [Company("ZZZZ", "?"), Company("AAPL", "IT")], [2024], known=set())
    assert [f["ticker"] for f in found.failures] == ["ZZZZ"]
    assert [c.ticker for c in found.candidates] == ["AAPL"]


def test_discovery_needs_years():
    with pytest.raises(IngestError, match="fiscal year"):
        discover_new_filings(make_client(FakeSession()), [Company("AAPL", "IT")], [], known=set())
