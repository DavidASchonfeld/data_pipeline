# One named error per layer, all sharing one base class, so a caller can catch exactly the stage that failed
# (or AgentDataError for "anything in this package") instead of guessing at raw library exceptions.

from __future__ import annotations

import importlib
from types import ModuleType


class AgentDataError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(AgentDataError):
    """A setting is missing or has a value I don't support (e.g. an unknown PLATFORM_PROFILE)."""


class MissingExtraError(AgentDataError):
    """An optional dependency group isn't installed; the message names the exact pip command to fix it."""


class BackendNotBuiltError(AgentDataError):
    """A backend was selected by config but its real implementation doesn't exist yet (a placeholder stub)."""


class IngestError(AgentDataError):
    """Fetching or landing raw source documents failed."""


class ParseError(AgentDataError):
    """Turning a raw document into structured sections/elements failed."""


class LakeError(AgentDataError):
    """Reading or writing a lake table (local folder, s3://, abfss://) failed."""


class EmbedError(AgentDataError):
    """Translating text into meaning vectors failed."""


class SearchIndexError(AgentDataError):
    """Writing to or querying the search index failed."""


class LLMProviderError(AgentDataError):
    """A call to a hosted language model failed."""


class SnowflakeError(AgentDataError):
    """Connecting to or querying Snowflake failed."""


class ServiceError(AgentDataError):
    """The HTTP/MCP service layer could not handle a request."""


class AgentError(AgentDataError):
    """The agent loop could not finish a question (tool failure, budget stop, bad model output)."""


def require_extra(module: str, extra: str) -> ModuleType:
    # Import an optional dependency, turning "module not found" into a clear "install agent-data[extra]" message.
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise MissingExtraError(
            f"'{module}' could not be imported (missing: {exc.name!r}). "
            f"Install the optional dependencies with: pip install 'agent-data[{extra}]'"
        ) from exc
