-- Staging view for SEC EDGAR financials — renames nothing (RAW columns are already clean), casts types
-- tag:stocks targets this model when dag_stocks.py runs `dbt run --select tag:stocks`
--
-- ── WHY IT'S CALLED "STAGING" ────────────────────────────────────────────────
-- Standard data engineering term for the cleanup layer between raw and final.
-- "Staging" here means the same as staging a deployment: data is being prepared
-- and made ready before it moves somewhere permanent. Nothing business-logic
-- runs here — just type fixes and renames. See dbt_project.yml for full context.
--
-- ── WHY STAGING EXISTS ────────────────────────────────────────────────────────
-- RAW stores data exactly as it arrived from the SEC API — column types are loose
-- (values come in as strings/JSON variants, dates are plain text). Before any
-- business logic can run, those types need to be fixed: text -> proper dates,
-- JSON variants -> floats, etc.
--
-- Staging is the cleanup layer: it does nothing except take RAW's messy format
-- and output a clean, consistently typed version. It never changes rows, never
-- filters for business rules — just standardises format.
--
-- Stored as a VIEW (not a table) — zero data stored, zero cost to maintain.
-- Every downstream model reads from this view instead of from RAW, so type-
-- casting logic lives in exactly one place. If the SEC API ever changes a
-- column type, the fix goes here and nothing else in the pipeline changes.
--
-- ── WHY THIS IS SEPARATE FROM MARTS ──────────────────────────────────────────
-- Staging and marts solve different problems:
--   • Staging -- fix the format (types, renames). No business logic.
--   • Marts   -- apply the rules (deduplication, FY filter, final shape).
--
-- Keeping them separate means a bug has a clear home: type mismatch -> staging,
-- wrong dedup behaviour -> marts. Mixing both into one model makes bugs harder
-- to find and makes it impossible to reuse the clean typed data elsewhere.
{{
    config(
        materialized='view',
        tags=['stocks']
    )
}}

select
    ticker,
    cik,
    entity_name,
    metric,
    label,
    try_to_date(period_end)  as period_end,   -- safe cast: returns NULL on bad values instead of erroring
    try_cast(value as float) as value,         -- RAW stores value as variant/string from JSON — cast to FLOAT
    try_to_date(filed_date)  as filed_date,
    form_type,
    fiscal_year,
    fiscal_period,
    frame
from {{ source('raw', 'COMPANY_FINANCIALS') }}  -- resolves to PIPELINE_DB.RAW.COMPANY_FINANCIALS
where ticker is not null
  and metric is not null  -- drop malformed rows before they reach marts
