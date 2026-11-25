# Tests for agent_data.config: profile defaults, per-component overrides, env var names shared with genai/,
# secret handling, and readable ConfigErrors for bad values. Offline; no .env file is read (env_file=None).

from __future__ import annotations

import pytest

from agent_data.config import (
    LOCAL_LAKE_URI,
    PROFILE_DEFAULTS,
    AgentDataSettings,
    load_settings,
    resolve_components,
)
from agent_data.engine._shared.errors import ConfigError
from agent_data.engine._shared.snowflake import SnowflakeSettings


def _settings() -> AgentDataSettings:
    # Load settings from the (cleaned, monkeypatched) environment only.
    return load_settings(env_file=None)


def test_default_profile_is_local():
    resolved = resolve_components(_settings())
    assert resolved.profile == "local"
    assert resolved.lake_uri == LOCAL_LAKE_URI
    assert resolved.search_backend == "pgvector"
    assert resolved.embedder == "local"


@pytest.mark.parametrize("profile", sorted(PROFILE_DEFAULTS))
def test_each_profile_resolves_to_its_defaults(clean_env, profile):
    clean_env.setenv("PLATFORM_PROFILE", profile)
    resolved = resolve_components(_settings())
    defaults = PROFILE_DEFAULTS[profile]
    assert resolved.profile == profile
    assert resolved.search_backend == defaults.search_backend
    assert resolved.embedder == defaults.embedder
    assert resolved.llm_provider == defaults.llm_provider
    assert resolved.agent_runner == defaults.agent_runner


def test_profile_and_backend_names_ignore_case_and_whitespace(clean_env):
    clean_env.setenv("PLATFORM_PROFILE", "  AWS ")
    clean_env.setenv("EMBEDDER", "Azure_OpenAI")
    resolved = resolve_components(_settings())
    assert resolved.profile == "aws"
    assert resolved.embedder == "azure_openai"


def test_blank_profile_falls_back_to_local(clean_env):
    clean_env.setenv("PLATFORM_PROFILE", "")
    assert resolve_components(_settings()).profile == "local"


def test_override_beats_profile_default(clean_env):
    # A mix: the AWS profile, but Azure AI Search and a LangGraph runner.
    clean_env.setenv("PLATFORM_PROFILE", "aws")
    clean_env.setenv("SEARCH_BACKEND", "azure_ai_search")
    clean_env.setenv("AGENT_RUNNER", "langgraph")
    clean_env.setenv("LAKE_URI", "s3://My-Bucket/lake")
    resolved = resolve_components(_settings())
    assert resolved.search_backend == "azure_ai_search"
    assert resolved.agent_runner == "langgraph"
    assert resolved.lake_uri == "s3://My-Bucket/lake"   # case kept: bucket names are case-sensitive
    assert resolved.embedder == "local"                 # untouched components keep the profile default


def test_blank_override_means_use_the_default(clean_env):
    clean_env.setenv("PLATFORM_PROFILE", "azure")
    clean_env.setenv("SEARCH_BACKEND", "  ")
    clean_env.setenv("LAKE_URI", "")
    resolved = resolve_components(_settings())
    assert resolved.search_backend == "azure_ai_search"
    assert resolved.lake_uri is None


def test_unknown_profile_raises_config_error_listing_choices(clean_env):
    clean_env.setenv("PLATFORM_PROFILE", "gcp")
    with pytest.raises(ConfigError) as info:
        _settings()
    message = str(info.value)
    assert "PLATFORM_PROFILE" in message
    assert "'local'" in message and "'aws'" in message and "'azure'" in message


def test_unknown_backend_override_raises_config_error(clean_env):
    clean_env.setenv("SEARCH_BACKEND", "elasticsearch")
    with pytest.raises(ConfigError, match="SEARCH_BACKEND"):
        _settings()


def test_bad_number_raises_config_error(clean_env):
    clean_env.setenv("PGVECTOR_PORT", "not-a-port")
    with pytest.raises(ConfigError, match="PGVECTOR_PORT"):
        _settings()


def test_reads_the_same_env_var_names_as_genai(clean_env):
    # Existing K8s secrets set these names, so agent_data must read them unchanged.
    clean_env.setenv("SNOWFLAKE_ACCOUNT", "acct-1")
    clean_env.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "/keys/rsa.p8")
    clean_env.setenv("PGVECTOR_HOST", "pgvector.svc")
    clean_env.setenv("LLM_MODEL", "some-model")
    clean_env.setenv("EMBEDDING_MODEL", "some-embedder")
    s = _settings()
    assert s.snowflake.account == "acct-1"
    assert s.snowflake.private_key_path == "/keys/rsa.p8"
    assert s.pgvector.host == "pgvector.svc"
    assert s.llm.model == "some-model"
    assert s.embedding.model == "some-embedder"


def test_snowflake_group_is_accepted_by_the_engine_helper():
    # The engine's connect() takes a SnowflakeSettings; the env-backed group must be one.
    assert isinstance(_settings().snowflake, SnowflakeSettings)


def test_secrets_never_appear_in_repr_or_dump(clean_env):
    clean_env.setenv("LLM_API_KEY", "sk-very-secret")
    clean_env.setenv("PGVECTOR_PASSWORD", "hunter2")
    s = _settings()
    text = repr(s) + str(s.model_dump())
    assert "sk-very-secret" not in text
    assert "hunter2" not in text
    assert s.llm.api_key.get_secret_value() == "sk-very-secret"   # still usable when explicitly asked for


def test_env_file_is_read_when_given(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PLATFORM_PROFILE=aws\nPGVECTOR_HOST=from-file\n")
    s = load_settings(env_file=env_file)
    assert s.platform_profile == "aws"
    assert s.pgvector.host == "from-file"


def test_real_env_var_beats_env_file(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PLATFORM_PROFILE=aws\n")
    clean_env.setenv("PLATFORM_PROFILE", "azure")
    assert load_settings(env_file=env_file).platform_profile == "azure"


def test_kubernetes_service_link_port_is_accepted(clean_env):
    # K8s sets PGVECTOR_PORT=tcp://<ip>:<port> in pods next to a "pgvector" Service; found on the live box.
    clean_env.setenv("PGVECTOR_PORT", "tcp://10.43.32.52:5432")
    assert _settings().pgvector.port == 5432


def test_a_genuinely_bad_port_is_still_rejected(clean_env):
    clean_env.setenv("PGVECTOR_PORT", "tcp://10.43.32.52:http")
    with pytest.raises(ConfigError, match="PGVECTOR_PORT"):
        _settings()
