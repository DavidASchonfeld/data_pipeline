# Snowflake connection for the in-process agent tools (query_snowflake_marts and friends).
#
# The standalone runners (summarize_runner.py, extract_runner.py) each carry their own byte-identical
# copy of this logic on purpose: each is a `python -m` subprocess entry point and stays self-contained.
# The agent is different — it is a library imported in-process by the orchestrator (and later the
# dashboard), so it gets ONE shared helper here instead of adding a fourth copy. I deliberately leave
# the existing runners untouched to avoid disturbing working EPIC 4/5 code and their tests.

from __future__ import annotations

import os


def _load_private_key_der() -> bytes:
    # Read the RSA private key file and return DER bytes — the format snowflake-connector wants.
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    key_path = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as f:
        p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_snowflake_conn():
    # Open a Snowflake connection using env vars + RSA key-pair auth (no MFA prompt for the service account).
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=_load_private_key_der(),
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),
    )
