# The ONE Snowflake connection helper for agent_data: RSA key-pair login (no MFA prompt for the service
# account), explicit timeouts, and a single SnowflakeError for every failure.
# It takes a plain SnowflakeSettings object, so any project can use it without this repo's config.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_data.engine._shared.errors import SnowflakeError, require_extra

# Fields that must be filled before I try to connect, mapped to the env var that usually sets them.
_REQUIRED = {
    "account": "SNOWFLAKE_ACCOUNT",
    "user": "SNOWFLAKE_USER",
    "private_key_path": "SNOWFLAKE_PRIVATE_KEY_PATH",
    "database": "SNOWFLAKE_DATABASE",
    "warehouse": "SNOWFLAKE_WAREHOUSE",
}


class SnowflakeSettings(BaseModel):
    """Everything needed to open a Snowflake connection. Empty strings mean "not set yet"."""

    account: str = ""
    user: str = ""
    private_key_path: str = ""
    database: str = ""
    warehouse: str = ""
    role: str = "PIPELINE_ROLE"
    # How long to wait for login, and for any single network call, before failing fast.
    login_timeout_seconds: int = Field(default=30, gt=0)
    network_timeout_seconds: int = Field(default=60, gt=0)


def missing_settings(settings: SnowflakeSettings) -> list[str]:
    # List the env var names for any required setting that is still empty.
    return [env for field, env in _REQUIRED.items() if not getattr(settings, field).strip()]


def load_private_key_der(path: str) -> bytes:
    # Read an unencrypted PEM private key and return DER bytes, the format the Snowflake connector expects.
    serialization = require_extra("cryptography.hazmat.primitives.serialization", "snowflake")
    try:
        with open(path, "rb") as handle:
            pem = handle.read()
    except OSError as exc:
        raise SnowflakeError(f"Could not read the Snowflake private key file at {path!r}: {exc.strerror}") from exc
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        # The parse error text can echo key details, so I report only the file and the error type.
        raise SnowflakeError(
            f"The file at {path!r} is not a valid unencrypted PEM private key ({type(exc).__name__})"
        ) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(settings: SnowflakeSettings) -> Any:
    # Open and return a Snowflake connection; the connector does its own network retries, so I add none.
    missing = missing_settings(settings)
    if missing:
        raise SnowflakeError(f"Snowflake is not configured; set: {', '.join(missing)}")
    connector = require_extra("snowflake.connector", "snowflake")
    private_key = load_private_key_der(settings.private_key_path)
    try:
        return connector.connect(
            account=settings.account,
            user=settings.user,
            private_key=private_key,
            database=settings.database,
            warehouse=settings.warehouse,
            role=settings.role,
            login_timeout=settings.login_timeout_seconds,
            network_timeout=settings.network_timeout_seconds,
        )
    except Exception as exc:  # the connector raises many types; I surface them all as one, with the cause chained
        raise SnowflakeError(
            f"Could not connect to Snowflake account {settings.account!r} as {settings.user!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
