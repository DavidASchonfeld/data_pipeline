# One settings object for agent_data. PLATFORM_PROFILE (local / aws / azure) picks a default backend for
# every component; a per-component env var (LAKE_URI, SEARCH_BACKEND, EMBEDDER, LLM_PROVIDER, AGENT_RUNNER)
# overrides just that one, so mixes like "AWS lake + Azure search" work.
# Values come from the environment (K8s secrets in production) and, for local dev, a gitignored .env file.
# Env var names match the older genai/ package (SNOWFLAKE_*, PGVECTOR_*, LLM_*) so existing secrets work as-is.

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_data.engine._shared.errors import ConfigError
from agent_data.engine._shared.logging import redact
from agent_data.engine._shared.snowflake import SnowflakeSettings

# The allowed values for each choice; anything else is rejected with a ConfigError listing these.
Profile = Literal["local", "aws", "azure"]
SearchBackend = Literal["pgvector", "azure_ai_search"]
Embedder = Literal["local", "azure_openai"]
LLMProviderName = Literal["anthropic", "openai", "azure_openai"]
AgentRunnerName = Literal["native", "langgraph"]

# Where the local profile keeps its lake tables: a gitignored .lake/ folder at the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_LAKE_URI = str(_REPO_ROOT / ".lake")

# Shared settings behaviour: read real env vars first, ignore unrelated ones, match names case-insensitively.
_ENV_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)


class SnowflakeEnv(BaseSettings, SnowflakeSettings):
    """Snowflake settings read from SNOWFLAKE_* env vars; it *is* a SnowflakeSettings, so the engine accepts it."""

    model_config = SettingsConfigDict(**_ENV_CONFIG, env_prefix="SNOWFLAKE_")


class PgvectorEnv(BaseSettings):
    """pgvector (Postgres) connection settings from PGVECTOR_* env vars; used by the search index backend."""

    model_config = SettingsConfigDict(**_ENV_CONFIG, env_prefix="PGVECTOR_")

    host: str = "localhost"
    port: int = Field(default=5432, gt=0, lt=65536)
    user: str = "pgvector"
    password: SecretStr = SecretStr("")   # never printed; set it in .env or the K8s secret
    db: str = "pgvector"
    connect_timeout_seconds: int = Field(default=10, gt=0)


class LLMEnv(BaseSettings):
    """Hosted language-model settings from LLM_* env vars (the provider choice itself is LLM_PROVIDER)."""

    model_config = SettingsConfigDict(**_ENV_CONFIG, env_prefix="LLM_")

    model: str = "claude-sonnet-4-5"
    api_key: SecretStr = SecretStr("")    # never printed; must come from the environment or a secret
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)


class EmbeddingEnv(BaseSettings):
    """Embedding-model settings from EMBEDDING_* env vars (the backend choice itself is EMBEDDER)."""

    model_config = SettingsConfigDict(**_ENV_CONFIG, env_prefix="EMBEDDING_")

    model: str = "all-MiniLM-L6-v2"


class AgentDataSettings(BaseSettings):
    """The profile, the optional per-component overrides, and each component's own settings group."""

    model_config = _ENV_CONFIG

    platform_profile: Profile = "local"
    lake_uri: str | None = None
    search_backend: SearchBackend | None = None
    embedder: Embedder | None = None
    llm_provider: LLMProviderName | None = None
    agent_runner: AgentRunnerName | None = None

    snowflake: SnowflakeEnv = Field(default_factory=SnowflakeEnv)
    pgvector: PgvectorEnv = Field(default_factory=PgvectorEnv)
    llm: LLMEnv = Field(default_factory=LLMEnv)
    embedding: EmbeddingEnv = Field(default_factory=EmbeddingEnv)

    @field_validator("platform_profile", "search_backend", "embedder", "llm_provider", "agent_runner",
                     mode="before")
    @classmethod
    def _normalise_choice(cls, value: Any, info: Any) -> Any:
        # Accept any capitalisation ("AWS" == "aws"); a blank override means "not set", a blank profile means local.
        if isinstance(value, str):
            value = value.strip().lower()
            if not value:
                return "local" if info.field_name == "platform_profile" else None
        return value

    @field_validator("lake_uri", mode="before")
    @classmethod
    def _blank_uri_is_unset(cls, value: Any) -> Any:
        # A blank LAKE_URI means "use the profile default"; case is kept because paths and buckets are case-sensitive.
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


class ProfileDefaults(BaseModel):
    """The backend each component uses when no override is set."""

    model_config = ConfigDict(frozen=True)

    lake_uri: str | None
    search_backend: SearchBackend
    embedder: Embedder
    llm_provider: LLMProviderName
    agent_runner: AgentRunnerName


# What each profile means. The aws/azure lake URIs stay unset until their storage exists in Terraform;
# until then LAKE_URI must be set explicitly to use a lake on those profiles.
PROFILE_DEFAULTS: dict[str, ProfileDefaults] = {
    "local": ProfileDefaults(lake_uri=LOCAL_LAKE_URI, search_backend="pgvector", embedder="local",
                             llm_provider="anthropic", agent_runner="native"),
    "aws": ProfileDefaults(lake_uri=None, search_backend="pgvector", embedder="local",
                           llm_provider="anthropic", agent_runner="native"),
    "azure": ProfileDefaults(lake_uri=None, search_backend="azure_ai_search", embedder="azure_openai",
                             llm_provider="azure_openai", agent_runner="native"),
}


class ResolvedComponents(BaseModel):
    """The final backend choice per component after applying profile defaults and overrides."""

    model_config = ConfigDict(frozen=True)

    profile: Profile
    lake_uri: str | None
    search_backend: SearchBackend
    embedder: Embedder
    llm_provider: LLMProviderName
    agent_runner: AgentRunnerName


def load_settings(env_file: str | Path | None = ".env") -> AgentDataSettings:
    # Read settings from the environment (+ optional .env file); every bad value is reported in one ConfigError.
    problems: list[str] = []
    groups = {name: _load_group(cls, env_file, problems) for name, cls in _GROUPS.items()}
    top = _load_group(AgentDataSettings, env_file, problems, **groups) if not problems else None
    if problems or top is None:
        raise ConfigError(f"Invalid agent_data settings: {'; '.join(problems)}")
    return top


def resolve_components(settings: AgentDataSettings) -> ResolvedComponents:
    # Merge the profile's defaults with any per-component overrides; an override always wins.
    defaults = PROFILE_DEFAULTS.get(settings.platform_profile)
    if defaults is None:  # only reachable if someone builds settings by hand and bypasses validation
        raise ConfigError(
            f"Unknown PLATFORM_PROFILE {settings.platform_profile!r}; choose one of {sorted(PROFILE_DEFAULTS)}"
        )
    return ResolvedComponents(
        profile=settings.platform_profile,
        lake_uri=settings.lake_uri or defaults.lake_uri,
        search_backend=settings.search_backend or defaults.search_backend,
        embedder=settings.embedder or defaults.embedder,
        llm_provider=settings.llm_provider or defaults.llm_provider,
        agent_runner=settings.agent_runner or defaults.agent_runner,
    )


# Each settings group by its field name on AgentDataSettings, loaded one by one so errors name the env var.
_GROUPS: dict[str, type[BaseSettings]] = {
    "snowflake": SnowflakeEnv,
    "pgvector": PgvectorEnv,
    "llm": LLMEnv,
    "embedding": EmbeddingEnv,
}


def _load_group(cls: type[BaseSettings], env_file: str | Path | None, problems: list[str], **values: Any) -> Any:
    # Build one settings class; on bad input, record readable problems instead of raising straight away.
    try:
        return cls(_env_file=env_file, **values)
    except ValidationError as exc:
        problems.extend(_describe_errors(exc, cls.model_config.get("env_prefix", "")))
        return None


def _describe_errors(exc: ValidationError, env_prefix: str) -> list[str]:
    # Turn pydantic's errors into "ENV_VAR: problem (got 'x')" lines, masking values of secret-looking fields.
    pieces = []
    for err in exc.errors():
        field = "_".join(str(part) for part in err.get("loc", ())) or "settings"
        env_name = f"{env_prefix}{field}".upper()
        shown = redact({env_name: err.get("input")})[env_name]
        pieces.append(f"{env_name}: {err.get('msg', 'invalid value')} (got {shown!r})")
    return pieces
