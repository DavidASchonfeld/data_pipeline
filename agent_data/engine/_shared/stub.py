# A placeholder backend: the factories return one of these when config selects a backend whose real
# implementation isn't written yet. It remembers what was chosen (so wiring can be tested end to end)
# and fails loudly the moment anything tries to actually use it.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_data.engine._shared.errors import BackendNotBuiltError


@dataclass(frozen=True)
class StubBackend:
    """Stands in for a not-yet-built backend; any real use raises BackendNotBuiltError."""

    component: str          # which stage this is for, e.g. "embedder"
    backend: str            # which backend config picked, e.g. "local"
    settings: Any = None    # the settings the real backend will receive, kept for inspection in tests

    # Lets callers and tests tell a placeholder apart from a real backend without try/except.
    is_stub = True

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes that don't exist, i.e. real backend methods like .search() or .embed().
        if name.startswith("__"):
            raise AttributeError(name)  # keep Python internals (copy, pickle, repr probes) working normally
        raise BackendNotBuiltError(
            f"The {self.component!r} backend {self.backend!r} is selected but not built yet "
            f"(tried to use .{name}). Pick another backend or wait for its implementation."
        )
