# Python Libraries
import json
import os
from typing import Any


# ── Financial concepts to extract from XBRL data ────────────────────────────
# Loaded from config/financial_concepts.json — edit that file to add/remove concepts without touching code
# Each object has keys: concept (XBRL tag), label (human-readable name), unit (USD or USD/shares)
_CONCEPTS_PATH = os.path.join(os.path.dirname(__file__), "config", "financial_concepts.json")
with open(_CONCEPTS_PATH) as _f:
    FINANCIAL_CONCEPTS: list[dict] = json.load(_f)  # list of {concept, label, unit} dicts


def flatten_company_financials(
    ticker: str,
    raw_response: dict[str, Any],
    annual_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Flatten nested XBRL companyfacts JSON into a list of row-dicts for SQL.

    Each dict in the returned list maps to one financial metric for one period
    and is safe to pass directly into pd.DataFrame() or Snowflake's write_pandas().

    Parameters
    ----------
    ticker       : Original ticker symbol (e.g. "AAPL") — stored alongside CIK for readability
    raw_response : Raw JSON from fetch_company_facts()
    annual_only  : If True, only keep 10-K annual filings (skip quarterly 10-Q)

    Returns
    -------
    List of dicts, one per metric per period:
        [{ "ticker", "cik", "entity_name", "metric", "label", "period_end",
           "value", "filed_date", "form_type", "fiscal_year", "fiscal_period",
           "frame" }, ...]
    """
    # Extract top-level metadata from SEC response
    cik = str(raw_response["cik"])
    entity_name = raw_response.get("entityName", "Unknown")
    gaap_facts = raw_response["facts"]["us-gaap"]

    records: list[dict[str, Any]] = []

    for item in FINANCIAL_CONCEPTS:
        xbrl_concept, human_label, expected_unit = item["concept"], item["label"], item["unit"]  # unpack from JSON object
        # Not every company reports every concept — skip gracefully if missing
        if xbrl_concept not in gaap_facts:
            continue

        concept_data = gaap_facts[xbrl_concept]
        units = concept_data.get("units", {})

        # Find the matching unit key (e.g. "USD" or "USD/shares")
        if expected_unit not in units:
            continue

        for entry in units[expected_unit]:
            # Filter to 10-K (annual) filings only — cleaner dataset, less noise
            if annual_only and entry.get("form") != "10-K":
                continue

            records.append({
                "ticker": ticker,
                "cik": cik,
                "entity_name": entity_name,
                "metric": xbrl_concept,
                # Human-readable label (e.g. "Revenue") for dashboards and reports
                "label": human_label,
                # "end" is the period end date (e.g. end of fiscal year)
                "period_end": entry.get("end", ""),
                # "val" is the actual dollar/share amount from the filing
                "value": entry.get("val"),
                # "filed" is when the company submitted the filing to the SEC
                "filed_date": entry.get("filed", ""),
                # "form" distinguishes 10-K (annual) from 10-Q (quarterly)
                "form_type": entry.get("form", ""),
                # Fiscal year and period help align data across companies
                "fiscal_year": entry.get("fy"),
                "fiscal_period": entry.get("fp", ""),
                # "frame" is SEC's calendar alignment tag (e.g. "CY2023")
                "frame": entry.get("frame", ""),
            })

    return records
