# Tests for the shared error types, the optional-dependency helper, and the placeholder backend.

from __future__ import annotations

import copy

import pytest

from agent_data.engine._shared import errors
from agent_data.engine._shared.errors import AgentDataError, BackendNotBuiltError, MissingExtraError, require_extra
from agent_data.engine._shared.stub import StubBackend

# Every layer error the package defines; each must be catchable as AgentDataError.
_LAYER_ERRORS = [
    errors.ConfigError, errors.MissingExtraError, errors.BackendNotBuiltError, errors.IngestError,
    errors.ParseError, errors.LakeError, errors.EmbedError, errors.SearchIndexError,
    errors.LLMProviderError, errors.SnowflakeError, errors.ServiceError, errors.AgentError,
]


@pytest.mark.parametrize("error_type", _LAYER_ERRORS)
def test_every_layer_error_shares_one_base(error_type):
    assert issubclass(error_type, AgentDataError)


def test_require_extra_returns_installed_module():
    assert require_extra("json", "unused").dumps({}) == "{}"


def test_require_extra_names_the_pip_command():
    with pytest.raises(MissingExtraError, match=r"pip install 'agent-data\[lake\]'"):
        require_extra("definitely_not_a_real_module_xyz", "lake")


def test_stub_records_its_choice_and_fails_on_use():
    stub = StubBackend(component="embedder", backend="local", settings={"model": "m"})
    assert (stub.component, stub.backend, stub.settings, stub.is_stub) == ("embedder", "local", {"model": "m"}, True)
    with pytest.raises(BackendNotBuiltError, match="'embedder' backend 'local'.*embed"):
        stub.embed(["text"])


def test_stub_keeps_python_internals_working():
    # copy/repr/hasattr on dunder names must not raise BackendNotBuiltError.
    stub = StubBackend(component="llm", backend="openai")
    assert copy.copy(stub) == stub
    assert "openai" in repr(stub)
    assert not hasattr(stub, "__not_a_real_dunder__")
