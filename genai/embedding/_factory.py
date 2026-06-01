from genai.config import EMBEDDING_PROVIDER
from genai.embedding.base import Embedder

# _SUPPORTED lists every valid value for EMBEDDING_PROVIDER so the error message is always up to date
_SUPPORTED: list[str] = ["local"]


def get_embedder() -> Embedder:
    # look up the chosen embedder and return a ready-to-use instance — callers never import a concrete class directly
    provider = EMBEDDING_PROVIDER.lower()

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
