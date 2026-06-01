#!/bin/bash
# Module: snowflake — run scripts/snowflake_setup.sql against a fresh or existing Snowflake account.
# Sourced by deploy.sh; all variables from common.sh are available here.
#
# Required env vars in .env.deploy (in addition to the normal SNOWFLAKE_* service-account vars):
#   SNOWFLAKE_ACCOUNT              — account identifier, e.g. abc12345.us-east-1
#   SNOWFLAKE_ADMIN_USER          — the ACCOUNTADMIN login the bootstrap connects as (a dedicated DEPLOY_USER
#                                   is recommended over a personal login — see scripts/deploy/setup_deploy_user.sh)
#   AND ONE OF:
#   SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH — path to an RSA .p8 key for that user (RECOMMENDED: bypasses MFA, runs
#                                   headlessly). Generate + register it with scripts/deploy/setup_deploy_user.sh.
#   SNOWFLAKE_ADMIN_PASSWORD      — admin password (LEGACY: fails if the user has MFA enrolled, which Snowflake
#                                   now enforces on password logins — error 250001/251012).
#
# All admin DDL is applied through scripts/deploy/apply_snowflake_sql.py (one place for connect + auth + split).

# Path to the shared SQL applier (handles key-pair-or-password auth + comment-strip + statement split).
_APPLY_SQL() { python3 "$PROJECT_ROOT/scripts/deploy/apply_snowflake_sql.py" "$1"; }

step_snowflake_setup() {
    echo "=== Snowflake Setup: applying scripts/snowflake_setup.sql ==="

    # Verify the account + admin user are present, plus at least one auth method.
    for var in SNOWFLAKE_ACCOUNT SNOWFLAKE_ADMIN_USER; do
        if [ -z "${!var:-}" ]; then
            echo "ERROR: $var is not set in .env.deploy — required for --snowflake-setup"
            exit 1
        fi
    done
    # Prefer key-pair auth (MFA-exempt, headless); fall back to password (blocked if the user has MFA).
    if [ -n "${SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH:-}" ]; then
        if [ ! -f "$SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH" ]; then
            echo "ERROR: SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH is set but the file is missing: $SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH"
            exit 1
        fi
        echo "Admin auth: RSA key-pair as $SNOWFLAKE_ADMIN_USER (MFA-exempt)."
    elif [ -n "${SNOWFLAKE_ADMIN_PASSWORD:-}" ]; then
        echo "Admin auth: password as $SNOWFLAKE_ADMIN_USER."
        echo "  NOTE: if this fails with 250001/251012, the user has MFA enrolled. Switch to key-pair:"
        echo "        scripts/deploy/setup_deploy_user.sh"
    else
        echo "ERROR: set SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH (recommended) or SNOWFLAKE_ADMIN_PASSWORD in .env.deploy"
        echo "  Run scripts/deploy/setup_deploy_user.sh to create the recommended headless DEPLOY_USER + key."
        exit 1
    fi

    SQL_FILE="$PROJECT_ROOT/scripts/snowflake_setup.sql"
    if [ ! -f "$SQL_FILE" ]; then
        echo "ERROR: SQL setup file not found: $SQL_FILE"
        exit 1
    fi

    # Ensure the connector + crypto (for key-pair auth) are available in the local Python that runs the applier.
    # Use `python3 -m pip` (not `pip3`) so the package lands in the exact Python apply_snowflake_sql.py runs under.
    python3 -m pip install --quiet snowflake-connector-python cryptography

    _APPLY_SQL "$SQL_FILE"

    # genai: when the GenAI layer is on, also create the ANALYTICS + MARTS tables the GenAI DAGs write to.
    # Skipped entirely when GENAI_ENABLED is not true, so the base setup is unchanged.
    if [ "${GENAI_ENABLED:-false}" = "true" ]; then
        _snowflake_genai_bootstrap
    fi

    echo "=== Snowflake Setup: done ==="
}

# Apply the GenAI admin DDL (ANALYTICS.FCT_FILING_EXTRACTS — EPIC 3/4, then MARTS.FCT_WEATHER_SUMMARIES — EPIC 5).
# Only reached when GENAI_ENABLED=true. Uses the same shared applier (so it inherits the key-pair auth above).
_snowflake_genai_bootstrap() {
    echo "=== Snowflake Setup (GenAI): applying ANALYTICS + MARTS bootstrap DDL ==="

    GENAI_SQL_FILE="$PROJECT_ROOT/airflow/dags/sql/analytics_bootstrap.sql"
    WEATHER_SQL_FILE="$PROJECT_ROOT/airflow/dags/sql/weather_summaries_table.sql"
    for f in "$GENAI_SQL_FILE" "$WEATHER_SQL_FILE"; do
        if [ ! -f "$f" ]; then
            echo "ERROR: GENAI_ENABLED=true but GenAI SQL file not found: $f"
            exit 1
        fi
    done

    _APPLY_SQL "$GENAI_SQL_FILE"      # ANALYTICS.FCT_FILING_EXTRACTS (EPIC 3/4)
    _APPLY_SQL "$WEATHER_SQL_FILE"    # MARTS.FCT_WEATHER_SUMMARIES (EPIC 5)

    echo "=== Snowflake Setup (GenAI): done ==="
}
