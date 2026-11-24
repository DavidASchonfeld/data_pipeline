# Tests for the shared logging helpers: namespace, secret masking, and key=value event lines.

from __future__ import annotations

import logging

from agent_data.engine._shared.logging import MASK, format_event, get_logger, log_event, redact


def test_loggers_live_under_the_package_namespace():
    assert get_logger("ingest").name == "agent_data.ingest"
    assert get_logger("agent_data.engine.x").name == "agent_data.engine.x"


def test_redact_masks_secret_looking_keys_only():
    out = redact({"PGVECTOR_PASSWORD": "p", "api_key": "k", "Authorization": "Bearer t", "host": "db", "rows": 3})
    assert out == {"PGVECTOR_PASSWORD": MASK, "api_key": MASK, "Authorization": MASK, "host": "db", "rows": 3}


def test_format_event_builds_a_key_value_line():
    assert format_event("fetched", ticker="AAPL", rows=2) == "fetched ticker='AAPL' rows=2"


def test_log_event_never_writes_a_secret(caplog):
    with caplog.at_level(logging.INFO, logger="agent_data"):
        log_event(get_logger("test"), "connected", host="db", password="hunter2")
    assert "hunter2" not in caplog.text
    assert "connected host='db' password='***'" in caplog.text
