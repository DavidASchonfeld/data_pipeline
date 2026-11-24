# Tests for agent_data.platform: every factory resolves on every profile, overrides reach the right
# backend, placeholders fail loudly on use, and a missing registry entry is a clear ConfigError.

from __future__ import annotations

import pytest

from agent_data import platform
from agent_data.config import PROFILE_DEFAULTS, load_settings
from agent_data.engine._shared.errors import BackendNotBuiltError, ConfigError


def _settings():
    # Load settings from the (cleaned, monkeypatched) environment only.
    return load_settings(env_file=None)


@pytest.mark.parametrize("profile", sorted(PROFILE_DEFAULTS))
@pytest.mark.parametrize("component", sorted(platform.FACTORIES))
def test_every_factory_resolves_on_every_profile(clean_env, profile, component):
    clean_env.setenv("PLATFORM_PROFILE", profile)
    backend = platform.FACTORIES[component](_settings())
    assert backend is not None
    assert backend.component == component


@pytest.mark.parametrize("profile", sorted(PROFILE_DEFAULTS))
def test_factories_pick_the_profile_backends(clean_env, profile):
    clean_env.setenv("PLATFORM_PROFILE", profile)
    s = _settings()
    defaults = PROFILE_DEFAULTS[profile]
    assert platform.get_embedder(s).backend == defaults.embedder
    assert platform.get_search_index(s).backend == defaults.search_backend
    assert platform.get_llm(s).backend == defaults.llm_provider
    assert platform.get_agent_runner(s).backend == defaults.agent_runner


def test_factories_read_settings_from_env_when_none_given(clean_env, monkeypatch):
    # Without an explicit settings object the factory loads them itself (no .env file in tests).
    monkeypatch.setattr(platform, "load_settings", lambda: load_settings(env_file=None))
    clean_env.setenv("PLATFORM_PROFILE", "azure")
    assert platform.get_search_index().backend == "azure_ai_search"


def test_override_reaches_the_factory(clean_env):
    clean_env.setenv("PLATFORM_PROFILE", "local")
    clean_env.setenv("LLM_PROVIDER", "openai")
    assert platform.get_llm(_settings()).backend == "openai"


def test_backend_receives_its_settings_group(clean_env):
    clean_env.setenv("PGVECTOR_HOST", "db.local")
    clean_env.setenv("LAKE_URI", "/tmp/lake")
    s = _settings()
    assert platform.get_search_index(s).settings.host == "db.local"
    assert platform.get_lake(s).settings == "/tmp/lake"


def test_placeholder_backend_fails_loudly_when_used():
    index = platform.get_search_index(_settings())
    assert index.is_stub is True
    with pytest.raises(BackendNotBuiltError, match="search_index.*pgvector.*search"):
        index.search("revenue")


def test_unregistered_backend_raises_config_error(monkeypatch):
    # Simulates a backend that config allows but nobody has registered a loader for.
    monkeypatch.delitem(platform.EMBEDDER_BACKENDS, "local")
    with pytest.raises(ConfigError, match="No embedder backend named 'local'"):
        platform.get_embedder(_settings())
