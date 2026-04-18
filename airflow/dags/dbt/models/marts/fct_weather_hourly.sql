-- Fact table for Open-Meteo hourly weather — dashboard-ready, deduplicated
-- RAW already deduplicates on insert in dag_weather.py, but this adds a safety layer in case of race conditions
-- tag:weather — dag_weather.py runs `dbt run --select tag:weather` after each Snowflake write
--
-- ── WHY IT'S CALLED "MARTS" ──────────────────────────────────────────────────
-- Short for "data marts" — the standard name for final, subject-specific tables
-- in a data warehouse. Each mart covers one topic; this one is weather readings.
-- The name fits: the dashboard and ML model come here to get exactly what they
-- need, pre-built and ready. See dbt_project.yml for the full three-layer pattern.
--
-- ── WHY MARTS EXISTS ─────────────────────────────────────────────────────────
-- Staging gave us clean timestamps and sensible column names. Marts applies the
-- one business rule this data needs: deduplication.
--
-- The pipeline ingests weather for multiple cities. Each city has its own row per
-- hour, so (observation_time, city_name) together form the unique identifier —
-- two cities can share the same timestamp, so partitioning by time alone would
-- wrongly collapse them. The ROW_NUMBER() window function partitions by both
-- columns and keeps only the latest-imported row per (time, city) pair.
--
-- Like all marts models, this is materialised as a TABLE. The dashboard reads
-- pre-computed rows at query time rather than re-running deduplication live.
-- For a table with millions of hourly readings across multiple cities, the
-- difference between a view (compute on every query) and a table (compute once
-- at pipeline run time) is the difference between a slow dashboard and a fast one.
--
-- ── THE TWO-LAYER CONTRACT ────────────────────────────────────────────────────
-- Staging handles format (epoch -> timestamp, column renames).
-- Marts handles rules (deduplication, final column selection).
-- Keeping them separate means each layer can be tested, debugged, and changed
-- independently — a type-conversion fix never risks breaking dedup logic.
{{
    config(
        materialized='table',
        tags=['weather']
    )
}}

with deduplicated as (
    select
        *,
        -- multi-city pipeline: (observation_time, city_name) is the composite primary key
        -- multiple cities share the same timestamp, so partition by both to dedup correctly
        row_number() over (
            partition by observation_time, city_name  -- dedup per (time, city) pair — multiple cities share the same timestamp
            order by imported_at desc nulls last
        ) as rn
    from {{ ref('stg_weather_hourly') }}  -- reads from PIPELINE_DB.STAGING.STG_WEATHER_HOURLY view
)

select
    observation_time,
    city_name,
    temperature_f,
    latitude,
    longitude,
    elevation,
    timezone,
    utc_offset_seconds,
    imported_at
from deduplicated
where rn = 1  -- drop duplicate rows
