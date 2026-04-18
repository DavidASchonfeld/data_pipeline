-- Fact table for annual SEC EDGAR financials — this is what the dashboard queries via PIPELINE_DB.MARTS
-- Deduplicates in case the same ticker/metric/period appears in multiple XBRL frames
-- tag:stocks — dag_stocks.py runs `dbt run --select tag:stocks` after writing to RAW
--
-- ── WHY IT'S CALLED "MARTS" ──────────────────────────────────────────────────
-- Short for "data marts" — the standard name for final, subject-specific tables
-- in a data warehouse. Each mart covers one topic; this one is company financials.
-- The name fits: the dashboard and ML model come here to get exactly what they
-- need, pre-built and ready. See dbt_project.yml for the full three-layer pattern.
--
-- ── WHY MARTS EXISTS ─────────────────────────────────────────────────────────
-- Staging produced a clean, correctly typed view of the data. Marts is the next
-- step: it applies the actual business rules that turn that clean data into
-- something the dashboard and ML model can query directly.
--
-- Two specific rules this model enforces:
--   1. Annual filings only (fiscal_period = 'FY'). SEC filings include quarterly
--      data (Q1, Q2, Q3) as well as full-year. The anomaly model and dashboard
--      only care about full-year figures. Filtering here means every downstream
--      consumer gets FY data automatically — they don't need to remember to filter.
--   2. Deduplication. A company can amend its SEC filing weeks or months after the
--      original submission. That creates two rows in RAW for the same company/year/
--      metric — the original and the amended version. The ROW_NUMBER() window
--      function keeps only the most recently filed row, so no downstream model
--      accidentally reads stale, superseded numbers.
--
-- Marts is materialised as a TABLE (not a view), meaning Snowflake pre-computes
-- and stores the result. The dashboard never runs deduplication logic at query
-- time — it just reads pre-built rows. That's what makes dashboard queries fast.
--
-- ── THE TWO-LAYER CONTRACT ────────────────────────────────────────────────────
-- Staging is read by marts (via {{ ref('stg_company_financials') }}).
-- Marts is read by the dashboard and the anomaly detector.
-- Nothing outside this pipeline reads RAW or STAGING directly.
-- That separation means: the source API format can change without touching
-- marts, and business rules can change without touching staging.
{{
    config(
        materialized='table',
        tags=['stocks']
    )
}}

with deduplicated as (
    select
        *,
        -- keep the most recently filed row; frame asc breaks ties deterministically when filed_date is the same date
        row_number() over (
            partition by ticker, metric, period_end, fiscal_period
            order by filed_date desc nulls last, frame asc nulls last
        ) as rn
    from {{ ref('stg_company_financials') }}  -- reads from PIPELINE_DB.STAGING.STG_COMPANY_FINANCIALS view
    where fiscal_period = 'FY'  -- annual filings only — matches annual_only=True in dag_stocks.py
)

-- column names intentionally match dashboard/db.py _load_ticker_data() query: metric, label, period_end, value, fiscal_year, fiscal_period
select
    ticker,
    cik,
    entity_name,
    metric,
    label,
    period_end,
    value,
    filed_date,
    form_type,
    fiscal_year,
    fiscal_period,
    frame
from deduplicated
where rn = 1  -- drop duplicate rows, keep only the keeper selected by the window function above
