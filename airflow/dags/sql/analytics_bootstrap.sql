-- GenAI ANALYTICS bootstrap — idempotent, safe to run on any fresh or existing account.
-- Applied by ./scripts/deploy.sh --snowflake-setup ONLY when GENAI_ENABLED=true (see scripts/deploy/snowflake.sh).
--
-- What this script creates:
--   ANALYTICS schema             — LLM-derived structured data (also auto-created by anomaly_detector.py)
--   ANALYTICS.FCT_FILING_EXTRACTS — structured facts the EPIC 4 extraction DAG pulls from 10-K filings
--   ANALYTICS.FCT_FILING_SECTIONS — cleaned full 10-K section text the EPIC 7 RAG ingest chunks + embeds
--
-- This is part of the GenAI layer (GENAI_ROADMAP.md, EPIC 3). When GENAI_ENABLED=false the table is
-- never created and the base pipeline is unaffected.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Schema — ANALYTICS holds LLM-derived structured output (alongside FCT_ANOMALIES)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.ANALYTICS
    COMMENT = 'LLM-derived structured data and anomaly detection results';

-- ─────────────────────────────────────────────────────────────────────────────
-- 1b. CREATE TABLE privilege — ANALYTICS may already exist owned by PIPELINE_ROLE (anomaly_detector.py
--     creates it at runtime as that role), so ACCOUNTADMIN can't assume ownership. Self-grant CREATE
--     TABLE first so the bootstrap works regardless of who owns the schema (ACCOUNTADMIN inherits
--     MANAGE GRANTS, which permits granting on objects it does not own).
-- ─────────────────────────────────────────────────────────────────────────────
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.ANALYTICS TO ROLE ACCOUNTADMIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. FCT_FILING_EXTRACTS — one row per (filing section, extract type); payload holds the LLM output
--    payload is VARIANT so each extract type can carry its own JSON shape without new columns.
--    model_name records the exact resolved model so a re-run is reproducible and auditable.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS PIPELINE_DB.ANALYTICS.FCT_FILING_EXTRACTS (
    ticker        VARCHAR,         -- company ticker the 10-K belongs to
    filing_date   DATE,            -- date the 10-K was filed with the SEC
    section       VARCHAR,         -- 10-K section the extract came from (e.g. "Item 1A - Risk Factors")
    extract_type  VARCHAR,         -- which extraction prompt produced the row (e.g. "risk_factors")
    payload       VARIANT,         -- the structured LLM output as JSON (shape varies per extract_type)
    model_name    VARCHAR,         -- exact resolved model id, for reproducibility and audit
    run_at        TIMESTAMP_NTZ    -- when the extraction ran
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2b. FCT_FILING_SECTIONS — the cleaned, HTML-stripped full text of each 10-K section, kept so the
--     EPIC 7 RAG ingest can chunk + embed it WITHOUT re-downloading the filing from EDGAR every run.
--     The EPIC 4 extractor writes this at fetch time (the text is already in memory). section_text is
--     a plain VARCHAR holding the UNtruncated section (Snowflake VARCHAR allows 16MB; sections run
--     50k–200k chars). Same (ticker, filing_date) dedup key as FCT_FILING_EXTRACTS.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS PIPELINE_DB.ANALYTICS.FCT_FILING_SECTIONS (
    ticker        VARCHAR,         -- company ticker the 10-K belongs to
    filing_date   DATE,            -- date the 10-K was filed with the SEC
    section       VARCHAR,         -- 10-K section the text came from (e.g. "Item 1A - Risk Factors", or "full")
    section_text  VARCHAR,         -- the cleaned, HTML-stripped section text, untruncated
    fetched_at    TIMESTAMP_NTZ    -- when the 10-K was downloaded
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Grants — the pipeline service role reads and writes this table (mirrors snowflake_setup.sql)
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA PIPELINE_DB.ANALYTICS TO ROLE PIPELINE_ROLE;
-- CREATE TABLE so the EPIC 4 extraction runner can CREATE TABLE IF NOT EXISTS at runtime, the same
-- way anomaly_detector.py does for FCT_ANOMALIES
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.ANALYTICS TO ROLE PIPELINE_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON PIPELINE_DB.ANALYTICS.FCT_FILING_EXTRACTS TO ROLE PIPELINE_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON PIPELINE_DB.ANALYTICS.FCT_FILING_SECTIONS TO ROLE PIPELINE_ROLE;
-- Cover future ANALYTICS tables too, so later GenAI tables are usable without re-granting
GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.ANALYTICS TO ROLE PIPELINE_ROLE;
