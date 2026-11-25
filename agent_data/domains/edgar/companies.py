# The companies and fiscal years the EDGAR ingest covers. A plain list so it is easy to read and review.
# 25 companies across all 11 stock-market sectors; the big banks (JPM, GS) file so often that their older 10-Ks
# fall off the SEC's "recent filings" list, which keeps the client's older-pages path honest.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    """One tracked company: its stock ticker and its sector (sector is for humans; the ingest only needs ticker)."""

    ticker: str
    sector: str


COMPANIES: tuple[Company, ...] = (
    Company("AAPL", "Information Technology"),  # Apple
    Company("MSFT", "Information Technology"),  # Microsoft
    Company("NVDA", "Information Technology"),  # NVIDIA
    Company("GOOGL", "Communication Services"),  # Alphabet (Google)
    Company("META", "Communication Services"),  # Meta Platforms (Facebook)
    Company("DIS", "Communication Services"),  # Walt Disney
    Company("AMZN", "Consumer Discretionary"),  # Amazon
    Company("HD", "Consumer Discretionary"),  # Home Depot
    Company("WMT", "Consumer Staples"),  # Walmart
    Company("KO", "Consumer Staples"),  # Coca-Cola
    Company("PG", "Consumer Staples"),  # Procter & Gamble
    Company("JNJ", "Health Care"),  # Johnson & Johnson
    Company("UNH", "Health Care"),  # UnitedHealth Group
    Company("PFE", "Health Care"),  # Pfizer
    Company("JPM", "Financials"),  # JPMorgan Chase
    Company("GS", "Financials"),  # Goldman Sachs
    Company("V", "Financials"),  # Visa
    Company("XOM", "Energy"),  # Exxon Mobil
    Company("CVX", "Energy"),  # Chevron
    Company("CAT", "Industrials"),  # Caterpillar
    Company("BA", "Industrials"),  # Boeing
    Company("UPS", "Industrials"),  # United Parcel Service
    Company("NEE", "Utilities"),  # NextEra Energy
    Company("PLD", "Real Estate"),  # Prologis
    Company("LIN", "Materials"),  # Linde
)

# Fiscal year = the calendar year the reporting period ENDED (Apple's FY2025 ended Sept 2025).
# A fixed window, so the evaluation questions (FY2023 and FY2025 filings) always stay covered.
DEFAULT_FISCAL_YEARS: tuple[int, ...] = (2023, 2024, 2025)

# Annual reports first; "/A" marks an amendment (a corrected or completed 10-K). Quarterly 10-Qs come later.
TARGET_FORMS: tuple[str, ...] = ("10-K", "10-K/A")
