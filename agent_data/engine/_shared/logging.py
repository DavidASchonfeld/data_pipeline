# Small logging helpers shared by every stage: one logger namespace ("agent_data") and key=value event lines
# with secrets masked, so logs stay greppable and never leak passwords, keys or tokens.
# As a library I never configure handlers myself; the app (DAG, API, script) decides where logs go.

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

# The parent logger every module in this package logs under.
ROOT_LOGGER_NAME = "agent_data"

# Any field whose name contains one of these words is masked before it is logged.
_SECRET_HINTS = ("password", "secret", "token", "api_key", "apikey", "private_key", "authorization", "credential")

# What a masked value looks like in the log line.
MASK = "***"

# Library best practice: attach a do-nothing handler so an app without logging setup sees no "no handler" warning.
logging.getLogger(ROOT_LOGGER_NAME).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    # Return a logger under the package namespace so one setting can quiet or raise all agent_data logs.
    if name == ROOT_LOGGER_NAME or name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    # Copy the fields, replacing any value whose key looks secret with a mask.
    return {key: (MASK if _looks_secret(key) else value) for key, value in fields.items()}


def format_event(event: str, **fields: Any) -> str:
    # Build one "event key=value key=value" line from already-redacted fields.
    parts = [event] + [f"{key}={value!r}" for key, value in redact(fields).items()]
    return " ".join(parts)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    # Log a named event with structured fields; secrets are masked before the line is built.
    if logger.isEnabledFor(level):
        logger.log(level, format_event(event, **fields))


def _looks_secret(key: str) -> bool:
    # True when the field name suggests it holds a credential.
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)
