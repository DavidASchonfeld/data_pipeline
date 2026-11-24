# The factories: the only place that turns settings into concrete backends. Call sites ask for
# get_embedder() / get_search_index() / ... and never import an adapter class, so swapping a backend is a
# config change. Each registry maps a backend name to a loader; a loader imports its adapter lazily, so an
# optional dependency is only needed when that backend is actually chosen.
# Backends that aren't built yet return a StubBackend placeholder; each later stage replaces one line here.

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_data.config import AgentDataSettings, load_settings, resolve_components
from agent_data.engine._shared.errors import ConfigError
from agent_data.engine._shared.stub import StubBackend

# A loader receives the full settings and returns a ready-to-use backend object.
Loader = Callable[[AgentDataSettings], Any]


def _stub(component: str, backend: str, pick_settings: Callable[[AgentDataSettings], Any]) -> Loader:
    # Build a loader that returns a placeholder carrying the settings the real backend will get.
    return lambda settings: StubBackend(component=component, backend=backend, settings=pick_settings(settings))


# One registry per component: backend name -> loader. The lake has one backend (Delta tables) whose
# storage location (folder, s3://, abfss://) is chosen by the URI, not by a separate backend name.
LAKE_BACKENDS: dict[str, Loader] = {
    "delta": _stub("lake", "delta", lambda s: resolve_components(s).lake_uri),
}
EMBEDDER_BACKENDS: dict[str, Loader] = {
    "local": _stub("embedder", "local", lambda s: s.embedding),
    "azure_openai": _stub("embedder", "azure_openai", lambda s: s.embedding),
}
SEARCH_INDEX_BACKENDS: dict[str, Loader] = {
    "pgvector": _stub("search_index", "pgvector", lambda s: s.pgvector),
    "azure_ai_search": _stub("search_index", "azure_ai_search", lambda s: None),
}
LLM_BACKENDS: dict[str, Loader] = {
    "anthropic": _stub("llm", "anthropic", lambda s: s.llm),
    "openai": _stub("llm", "openai", lambda s: s.llm),
    "azure_openai": _stub("llm", "azure_openai", lambda s: s.llm),
}
AGENT_RUNNER_BACKENDS: dict[str, Loader] = {
    "native": _stub("agent_runner", "native", lambda s: None),
    "langgraph": _stub("agent_runner", "langgraph", lambda s: None),
}


def get_lake(settings: AgentDataSettings | None = None) -> Any:
    # Return the lake store (Delta tables at the resolved LAKE_URI).
    return _build("lake", "delta", LAKE_BACKENDS, settings)


def get_embedder(settings: AgentDataSettings | None = None) -> Any:
    # Return the embedder: the "translator" that turns text into meaning vectors.
    s = settings or load_settings()
    return _build("embedder", resolve_components(s).embedder, EMBEDDER_BACKENDS, s)


def get_search_index(settings: AgentDataSettings | None = None) -> Any:
    # Return the search index that stores chunks and answers keyword + meaning queries.
    s = settings or load_settings()
    return _build("search_index", resolve_components(s).search_backend, SEARCH_INDEX_BACKENDS, s)


def get_llm(settings: AgentDataSettings | None = None) -> Any:
    # Return the hosted language-model provider.
    s = settings or load_settings()
    return _build("llm", resolve_components(s).llm_provider, LLM_BACKENDS, s)


def get_agent_runner(settings: AgentDataSettings | None = None) -> Any:
    # Return the agent loop that plans, calls tools and writes the answer.
    s = settings or load_settings()
    return _build("agent_runner", resolve_components(s).agent_runner, AGENT_RUNNER_BACKENDS, s)


# Every factory by component name, so tests and health checks can walk all of them in one loop.
FACTORIES: dict[str, Callable[[AgentDataSettings | None], Any]] = {
    "lake": get_lake,
    "embedder": get_embedder,
    "search_index": get_search_index,
    "llm": get_llm,
    "agent_runner": get_agent_runner,
}


def _build(component: str, backend: str, registry: dict[str, Loader], settings: AgentDataSettings | None) -> Any:
    # Look up the chosen backend's loader and run it; a missing entry is a clear config error, not a KeyError.
    loader = registry.get(backend)
    if loader is None:
        raise ConfigError(
            f"No {component} backend named {backend!r} is registered; available: {sorted(registry)}"
        )
    return loader(settings or load_settings())
