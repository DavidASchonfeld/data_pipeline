import threading

from genai.config import EMBEDDING_PROVIDER
from genai.embedding.base import Embedder

# _SUPPORTED lists every valid value for EMBEDDING_PROVIDER so the error message is always up to date
_SUPPORTED: list[str] = ["local"]

# Process-wide cache for the embedder. The local backend loads an ~80 MB model on first use, so
# building a fresh embedder on every call would reload that model every time — e.g. on every single
# retrieval query, which is exactly what blew the EPIC 8 latency budget. Memoizing one instance lets
# the model load once and be reused. Keyed by provider so a changed EMBEDDING_PROVIDER rebuilds; the
# lock stops two threads (e.g. concurrent dashboard requests at startup) racing to load the model twice.
_lock = threading.Lock()
_cached: Embedder | None = None
_cached_provider: str | None = None


def _build(provider: str) -> Embedder:
    # Construct a fresh embedder for the given provider (no model load yet — that stays lazy).
    if provider == "local":
        # import inside the function so sentence-transformers is only loaded when this backend is actually requested
        from genai.embedding.local_st import LocalSentenceTransformerEmbedder
        return LocalSentenceTransformerEmbedder()

    # unknown provider — tell the user exactly what values are accepted
    raise ValueError(
        f"Unknown embedding provider: '{provider}'. "
        f"Supported values for EMBEDDING_PROVIDER: {_SUPPORTED}. "
        "To add a new provider, create genai/embedding/<name>_embedder.py and add a branch here."
    )


def get_embedder() -> Embedder:
    # Return the shared embedder instance, building it once on first use (or when the provider changes).
    global _cached, _cached_provider
    provider = EMBEDDING_PROVIDER.lower()

    # Fast path: reuse the cached instance (with its already-loaded model) when the provider is unchanged.
    if _cached is not None and _cached_provider == provider:
        return _cached

    with _lock:
        # Re-check inside the lock so only the first thread through builds the instance.
        if _cached is None or _cached_provider != provider:
            _cached = _build(provider)
            _cached_provider = provider
    return _cached
