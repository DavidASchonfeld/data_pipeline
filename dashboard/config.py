"""Dashboard configuration — all environment variables and runtime constants in one place.

Mirrors the pattern used in airflow/dags/shared/config.py.
Other modules import from here instead of calling os.environ.get() directly,
so all configurable values are visible in one file.
"""

import os
from dotenv import load_dotenv  # reads .env for local dev; no-op in production

load_dotenv()

# ── Database credentials ──────────────────────────────────────────────────────
# Local dev:   set values in a .env file at the repo root (gitignored)
# Production:  set values in a Kubernetes Secret referenced by the Flask Deployment
SQL_USERNAME = os.environ.get("DB_USER",     "airflow_user")
SQL_PASSWORD = os.environ.get("DB_PASSWORD", "")
SQL_DATABASE = os.environ.get("DB_NAME",     "database_one")
SQL_URL      = os.environ.get("DB_HOST",     "")

# "mariadb" (default) or "snowflake" — controls which database engine is created
DB_BACKEND = os.environ.get("DB_BACKEND", "mariadb")

# ── Snowflake connection ──────────────────────────────────────────────────────
# Only used when DB_BACKEND = "snowflake" — set values in the Kubernetes Secret
SNOWFLAKE_ACCOUNT   = os.environ.get("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_USER      = os.environ.get("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD  = os.environ.get("SNOWFLAKE_PASSWORD")
SNOWFLAKE_DATABASE  = os.environ.get("SNOWFLAKE_DATABASE", "PIPELINE_DB")
SNOWFLAKE_SCHEMA    = os.environ.get("SNOWFLAKE_SCHEMA",   "MARTS")
SNOWFLAKE_WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "PIPELINE_WH")
SNOWFLAKE_ROLE      = os.environ.get("SNOWFLAKE_ROLE",     "PIPELINE_ROLE")

# ── Flask ─────────────────────────────────────────────────────────────────────
# Required — set in the Kubernetes Secret alongside database credentials
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
# Centralised here so adjusting cache lifetime only requires one change
CACHE_TTL_FINANCIALS = 3600   # 1 hour — SEC filings change at most daily
CACHE_TTL_WEATHER    = 900    # 15 min — matches Open-Meteo refresh cadence

# ── Security ──────────────────────────────────────────────────────────────────
# ALLOWED_ORIGINS: empty string = no cross-origin access; set in K8s secret if needed
ALLOWED_ORIGINS  = os.environ.get("ALLOWED_ORIGINS",  "")
# Credentials for the /validation endpoint — set in the Kubernetes Secret
VALIDATION_USER  = os.environ.get("VALIDATION_USER",  "")
VALIDATION_PASS  = os.environ.get("VALIDATION_PASS",  "")
