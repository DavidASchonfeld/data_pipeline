# Compatibility shim — edgar_client.py has been split into:
#   edgar_api.py        — rate limiter, CIK resolution, raw HTTP calls
#   edgar_transforms.py — flatten_company_financials(), XBRL pivot logic
# Import from those modules directly; this file re-exports for backward compatibility.
from edgar_api import (        # noqa: F401 — re-export for any remaining callers
    EDGAR_CONTACT_EMAIL,
    EDGAR_USER_AGENT,
    EDGAR_TICKERS_URL,
    EDGAR_COMPANY_FACTS_URL,
    RateLimiter,
    resolve_cik,
    fetch_company_facts,
)
from edgar_transforms import (  # noqa: F401 — re-export for any remaining callers
    FINANCIAL_CONCEPTS,
    flatten_company_financials,
)
