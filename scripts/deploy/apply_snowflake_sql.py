#!/usr/bin/env python3
# Apply one .sql file to Snowflake as ACCOUNTADMIN for the deploy bootstrap (./scripts/deploy.sh --snowflake-setup).
# Shared by every admin DDL step in scripts/deploy/snowflake.sh so the connect + comment-strip + split logic
# lives in exactly one place.
#
# Auth, in order of preference:
#   1. RSA key-pair  — when SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH points at a readable .p8 key. This BYPASSES MFA
#      (Snowflake does not layer MFA on key-pair auth), so the deploy runs headlessly. This is the path a
#      dedicated DEPLOY_USER uses — see scripts/deploy/setup_deploy_user.sh.
#   2. Password      — when only SNOWFLAKE_ADMIN_PASSWORD is set. Legacy; FAILS if the admin user has MFA
#      enrolled (Snowflake now enforces MFA on human password logins → error 250001/251012).
#
# Usage: python3 apply_snowflake_sql.py <path-to-sql-file>

import os
import re
import sys


def _load_private_key_der(key_path: str) -> bytes:
    # Read the RSA private key and hand snowflake-connector the DER bytes it wants (matches the runtime runner).
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    with open(key_path, "rb") as f:
        p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _connect():
    # Build the ACCOUNTADMIN connection, preferring MFA-exempt key-pair auth when a key path is configured.
    import snowflake.connector

    user = os.environ["SNOWFLAKE_ADMIN_USER"]
    kwargs = dict(account=os.environ["SNOWFLAKE_ACCOUNT"], user=user, role="ACCOUNTADMIN")

    key_path = os.environ.get("SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH", "").strip()
    if key_path:
        kwargs["private_key"] = _load_private_key_der(key_path)
        print(f"Connecting to Snowflake as {user} (RSA key-pair, MFA-exempt)...")
    elif os.environ.get("SNOWFLAKE_ADMIN_PASSWORD"):
        kwargs["password"] = os.environ["SNOWFLAKE_ADMIN_PASSWORD"]
        print(f"Connecting to Snowflake as {user} (password auth — set SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH to bypass MFA)...")
    else:
        raise SystemExit("ERROR: set SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH (recommended) or SNOWFLAKE_ADMIN_PASSWORD")

    return snowflake.connector.connect(**kwargs)


def main(sql_path: str) -> None:
    sql_final = open(sql_path).read()

    # Strip -- comments before splitting on semicolons, so a ';' inside a comment is not treated as a
    # statement boundary (same reasoning the inline heredocs used before this was factored out).
    sql_no_comments = re.sub(r"--[^\n]*", "", sql_final)
    statements = [s.strip() for s in sql_no_comments.split(";") if s.strip()]

    conn = _connect()
    try:
        cur = conn.cursor()
        total = len(statements)
        print(f"Executing {total} SQL statements from {os.path.basename(sql_path)}...")
        for i, stmt in enumerate(statements, 1):
            print(f"  [{i}/{total}] {stmt.splitlines()[0].strip()}")  # first line = readable progress
            cur.execute(stmt)
    finally:
        conn.close()
    print(f"Applied {os.path.basename(sql_path)} — objects created/verified.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: apply_snowflake_sql.py <path-to-sql-file>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
