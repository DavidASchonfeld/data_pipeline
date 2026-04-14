#!/bin/bash
# Module: snowflake — run scripts/snowflake_setup.sql against a fresh or existing Snowflake account.
# Sourced by deploy.sh; all variables from common.sh are available here.
#
# Required env vars in .env.deploy (in addition to the normal SNOWFLAKE_* service-account vars):
#   SNOWFLAKE_ADMIN_USER     — your personal Snowflake login (needs ACCOUNTADMIN — required to create users and schemas)
#   SNOWFLAKE_ADMIN_PASSWORD — admin password (never committed)
#   SNOWFLAKE_ACCOUNT        — account identifier, e.g. abc12345.us-east-1
#   SNOWFLAKE_PASSWORD       — desired password for PIPELINE_USER (injected into the SQL at run time)
#
# The script uses snowflake-connector-python (already a project dependency — installed in the
# anomaly-detection ml-venv and available via pip in most environments).

step_snowflake_setup() {
    echo "=== Snowflake Setup: applying scripts/snowflake_setup.sql ==="

    # Verify the required admin credentials are present before attempting a connection
    for var in SNOWFLAKE_ACCOUNT SNOWFLAKE_ADMIN_USER SNOWFLAKE_ADMIN_PASSWORD SNOWFLAKE_PASSWORD; do
        if [ -z "${!var:-}" ]; then
            echo "ERROR: $var is not set in .env.deploy — required for --snowflake-setup"
            echo "  SNOWFLAKE_ADMIN_USER / SNOWFLAKE_ADMIN_PASSWORD — your personal SYSADMIN credentials"
            echo "  SNOWFLAKE_PASSWORD                              — desired password for PIPELINE_USER"
            exit 1
        fi
    done

    # Read the SQL file and inject the PIPELINE_USER password before sending to Snowflake.
    # {{SNOWFLAKE_PASSWORD}} is a placeholder in the SQL — we replace it here so the password
    # is never stored in the file itself (which IS committed to git).
    SQL_FILE="$PROJECT_ROOT/scripts/snowflake_setup.sql"
    if [ ! -f "$SQL_FILE" ]; then
        echo "ERROR: SQL setup file not found: $SQL_FILE"
        exit 1
    fi

    # Ensure snowflake-connector-python is available locally (it lives in the EC2 ml-venv, not Mac system Python).
    # Use `python3 -m pip` (not `pip3`) so the package is installed into the exact Python that runs the next block.
    python3 -m pip install --quiet snowflake-connector-python

    # Run the setup SQL via Python (snowflake-connector-python is already a project dependency)
    python3 - <<PYTHON
import snowflake.connector
import os
import sys
import re

# Read the SQL file and replace the password placeholder with the real value
sql_raw = open("$SQL_FILE").read()
sql_final = sql_raw.replace("{{SNOWFLAKE_PASSWORD}}", os.environ["SNOWFLAKE_PASSWORD"])

# Connect as ACCOUNTADMIN — the top-level role in Snowflake. SYSADMIN cannot create schemas or users,
# so ACCOUNTADMIN is required for a full first-time setup (warehouses, databases, schemas, roles, users).
print("Connecting to Snowflake as ACCOUNTADMIN...")
conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_ADMIN_USER"],
    password=os.environ["SNOWFLAKE_ADMIN_PASSWORD"],
    role="ACCOUNTADMIN",  # must be ACCOUNTADMIN — SYSADMIN lacks CREATE SCHEMA and CREATE USER privileges
)

cur = conn.cursor()

# Remove -- comments before splitting on semicolons.
# Without this, a semicolon inside a comment (e.g. "landing zone; written by Airflow")
# would be treated as a statement boundary, sending the leftover comment text to Snowflake as SQL.
sql_no_comments = re.sub(r'--[^\n]*', '', sql_final)

# Split on semicolons; skip anything that is blank after stripping whitespace
statements = [s.strip() for s in sql_no_comments.split(";") if s.strip()]

total = len(statements)
print(f"Executing {total} SQL statements...")

for i, stmt in enumerate(statements, 1):
    # Show the first line of each statement so progress is readable in the log
    preview = stmt.splitlines()[0].strip()
    print(f"  [{i}/{total}] {preview}")
    cur.execute(stmt)

conn.close()
print("Snowflake setup complete — all objects created/verified.")
PYTHON

    echo "=== Snowflake Setup: done ==="
}
