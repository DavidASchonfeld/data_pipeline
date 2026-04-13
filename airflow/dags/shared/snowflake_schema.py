"""Snowflake fully-qualified identifiers — single source of truth for all table/schema names.

A rename or schema move only needs one edit here; all callers pick it up automatically.
"""

# ── Schema components ─────────────────────────────────────────────────────────
PIPELINE_DB      = "PIPELINE_DB"       # top-level Snowflake database
RAW_SCHEMA       = "RAW"               # landing zone: tables written directly by DAGs
MARTS_SCHEMA     = "MARTS"             # curated MART tables built by dbt
ANALYTICS_SCHEMA = "ANALYTICS"         # ML / derived tables (e.g. FCT_ANOMALIES)

# ── Fully-qualified table identifiers ─────────────────────────────────────────
# Format: <database>.<schema>.<table>
RAW_COMPANY_FINANCIALS  = f"{PIPELINE_DB}.{RAW_SCHEMA}.COMPANY_FINANCIALS"    # SEC EDGAR raw rows
RAW_WEATHER_HOURLY      = f"{PIPELINE_DB}.{RAW_SCHEMA}.WEATHER_HOURLY"        # Open-Meteo raw rows
MARTS_FCT_FINANCIALS    = f"{PIPELINE_DB}.{MARTS_SCHEMA}.FCT_COMPANY_FINANCIALS"  # dbt annual financials mart
MARTS_FCT_WEATHER       = f"{PIPELINE_DB}.{MARTS_SCHEMA}.FCT_WEATHER_HOURLY"  # dbt hourly weather mart
ANALYTICS_FCT_ANOMALIES = f"{PIPELINE_DB}.{ANALYTICS_SCHEMA}.FCT_ANOMALIES"   # IsolationForest results

# ── Convenience: schema-level identifiers ─────────────────────────────────────
# Used for CREATE SCHEMA IF NOT EXISTS statements (e.g. anomaly_detector.py)
ANALYTICS_SCHEMA_FULL   = f"{PIPELINE_DB}.{ANALYTICS_SCHEMA}"  # database.schema for DDL use
