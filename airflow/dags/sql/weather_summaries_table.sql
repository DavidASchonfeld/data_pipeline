-- GenAI MARTS bootstrap — idempotent, safe to run on any fresh or existing account.
-- Applied by ./scripts/deploy.sh --snowflake-setup ONLY when GENAI_ENABLED=true (see scripts/deploy/snowflake.sh).
--
-- What this script creates:
--   MARTS.FCT_WEATHER_SUMMARIES — one plain-English weather summary per city per week, written by the
--                                 EPIC 5 summarize_runner (genai/runners/summarize_runner.py)
--
-- This is part of the GenAI layer (GENAI_ROADMAP.md, EPIC 5). When GENAI_ENABLED=false the table is
-- never created and the base weather pipeline is unaffected.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. CREATE TABLE privilege — MARTS may already be owned by PIPELINE_ROLE (dbt creates its tables there
--    at runtime as that role), so ACCOUNTADMIN can't assume ownership. Self-grant CREATE TABLE first so
--    the bootstrap works regardless of who owns the schema (ACCOUNTADMIN inherits MANAGE GRANTS, which
--    permits granting on objects it does not own). Mirrors analytics_bootstrap.sql.
-- ─────────────────────────────────────────────────────────────────────────────
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.MARTS TO ROLE ACCOUNTADMIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. FCT_WEATHER_SUMMARIES — one row per (city, week_start). summary_text is the LLM's 2–4 sentence
--    plain-English description of that week's weather; model_name records the exact resolved model so a
--    re-run is reproducible and auditable. The runner keeps re-runs idempotent via a scoped
--    delete + insert on (city, week_start) — Snowflake has no enforced unique constraint.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES (
    city          VARCHAR,         -- city name (matches WEATHER_CITIES / dashboard ALLOWED_CITIES)
    week_start    DATE,            -- Monday of the summarized week
    summary_text  VARCHAR,         -- the LLM's 2–4 sentence plain-English summary
    model_name    VARCHAR,         -- exact resolved model id, for reproducibility and audit
    run_at        TIMESTAMP_NTZ    -- when the summary was generated
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Grants — the pipeline service role reads and writes this table (mirrors analytics_bootstrap.sql).
--    The base snowflake_setup.sql already grants FUTURE TABLES in MARTS to PIPELINE_ROLE, but an explicit
--    grant keeps this file self-sufficient and order-independent.
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
-- CREATE TABLE so the summarize runner can CREATE TABLE IF NOT EXISTS at runtime (self-sufficient runner)
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES TO ROLE PIPELINE_ROLE;
