# Shared test setup for agent_data: every test starts from a clean environment, so a developer's real
# .env or shell variables (API keys, a different PLATFORM_PROFILE) can never change a test's outcome.

from __future__ import annotations

import pytest

# Every env var name agent_data reads, directly or by prefix.
_EXACT = ("PLATFORM_PROFILE", "LAKE_URI", "SEARCH_BACKEND", "EMBEDDER", "LLM_PROVIDER", "AGENT_RUNNER")
_PREFIXES = ("SNOWFLAKE_", "PGVECTOR_", "LLM_", "EMBEDDING_", "EDGAR_")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    # Remove every agent_data env var for the duration of each test.
    import os

    for name in list(os.environ):
        if name.upper() in _EXACT or name.upper().startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch
