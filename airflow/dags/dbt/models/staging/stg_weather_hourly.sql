-- Staging view for Open-Meteo weather — converts epoch integer columns to TIMESTAMP_NTZ for readability
-- tag:weather targets this model when dag_weather.py runs `dbt run --select tag:weather`
--
-- ── WHY IT'S CALLED "STAGING" ────────────────────────────────────────────────
-- Standard data engineering term for the cleanup layer between raw and final.
-- "Staging" here means the same as staging a deployment: data is being prepared
-- and made ready before it moves somewhere permanent. Nothing business-logic
-- runs here — just type fixes and renames. See dbt_project.yml for full context.
--
-- ── WHY STAGING EXISTS ────────────────────────────────────────────────────────
-- Open-Meteo returns timestamps as "epoch seconds" — a raw integer like 1713312000
-- rather than a human-readable date like 2024-04-17 00:00:00. RAW stores that
-- integer as-is. Any SQL filtering "show me data from last week" would have to
-- convert that number every single time, in every model that touched weather data.
--
-- Staging fixes it once: epoch integers -> proper TIMESTAMP_NTZ values, raw
-- column names (time, temperature_2m) -> readable ones (observation_time,
-- temperature_f). Downstream models write plain SQL without touching format quirks.
--
-- Like the financials staging model, stored as a VIEW — zero cost, always reflects
-- current RAW data, single place to update if the source format ever changes.
--
-- ── WHY THIS IS SEPARATE FROM MARTS ──────────────────────────────────────────
-- Staging and marts solve different problems:
--   • Staging -- fix the format (type conversions, column renames, null guards).
--   • Marts   -- apply the rules (deduplication, final column selection).
--
-- Keeping them separate means clean typed data from staging can be reused by any
-- future model without duplicating conversion logic. Marts stays focused on
-- business rules, not format cleanup.
{{
    config(
        materialized='view',
        tags=['weather']
    )
}}

select
    to_timestamp(time)        as observation_time,  -- TIME is stored as epoch seconds (NUMBER) in RAW — convert to TIMESTAMP_NTZ
    temperature_2m            as temperature_f,      -- already in Fahrenheit (fahrenheit=True set in dag_weather.py extract)
    latitude,
    longitude,
    elevation,
    timezone,
    utc_offset_seconds,
    city_name,                                    -- city identifier from multi-city pipeline
    to_timestamp(imported_at) as imported_at         -- imported_at also stored as epoch seconds by snowflake_client.py
from {{ source('raw', 'WEATHER_HOURLY') }}  -- resolves to PIPELINE_DB.RAW.WEATHER_HOURLY
where time is not null  -- guard against partially written rows
  and city_name is not null  -- exclude legacy rows from before multi-city support
