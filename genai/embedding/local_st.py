from __future__ import annotations

import logging

from genai.config import EMBEDDING_MODEL
from genai.embedding.base import Embedder, EmbedderError

# Module logger — records model load + batch sizes, never the text being embedded (no PII).
logger = logging.getLogger(__name__)

# Preprocessing version. Bump this whenever the model OR the preprocessing changes (e.g. toggling
# normalization below), so every chunk's stored model_id stops matching and gets re-embedded. This
# is what makes a model swap "re-embed only stale rows" instead of "rebuild everything" (reference §2).
_MODEL_VERSION = 1

# Output width of all-MiniLM-L6-v2 — must match the pgvector(384) column; checked on first load.
_DIM = 384


class LocalSentenceTransformerEmbedder(Embedder):
    # __init__: record the model name but DON'T load the model — loading is deferred to first use so
    # importing this file is cheap and the ~80 MB model is only pulled when something actually embeds.
    def __init__(self) -> None:
        self._model = None
        self._model_name = EMBEDDING_MODEL

    def _ensure_model(self):
        # Lazy-load and cache the model object on first embed call; reused for every later batch.
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # deferred heavy import
            except ImportError as exc:
                raise EmbedderError(
                    "The 'sentence-transformers' package is not installed. "
                    "Run: pip install sentence-transformers  (or check that ml-venv was built with GENAI_ENABLED=true)"
                ) from exc

            # Downloads the model from HuggingFace on first run, then loads from the local cache thereafter.
            try:
                self._model = SentenceTransformer(self._model_name)
            except Exception as exc:  # network/download/load failure → one error type
                raise EmbedderError(f"failed to load embedding model '{self._model_name}': {exc}") from exc

            # Defensive: a mismatched model would silently write wrong-width vectors that pgvector rejects later.
            # The accessor was renamed get_sentence_embedding_dimension → get_embedding_dimension in newer
            # sentence-transformers; prefer the new name, fall back to the old, so either version works.
            if hasattr(self._model, "get_embedding_dimension"):
                actual = self._model.get_embedding_dimension()
            else:
                actual = self._model.get_sentence_embedding_dimension()
            if actual != _DIM:
                raise EmbedderError(f"model '{self._model_name}' has dim {actual}, expected {_DIM}")
            logger.info("loaded embedding model %s (dim=%d)", self._model_name, _DIM)
        return self._model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Empty input → empty output, without loading the model (keeps the no-op path cheap).
        if not texts:
            return []
        model = self._ensure_model()
        try:
            # normalize_embeddings=True makes cosine similarity (the pgvector vector_cosine_ops index)
            # a plain dot product — normalising once at the source keeps every downstream query correct.
            vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        except Exception as exc:  # any encode failure → one error type with context
            raise EmbedderError(f"embedding failed (model={self._model_name}): {exc}") from exc
        # tolist() converts the numpy array into plain Python floats so the runner can JSON-serialize it.
        return vectors.tolist()

    @property
    def dim(self) -> int:
        # A constant — readable without loading the model, so callers can size storage up front.
        return _DIM

    @property
    def model_id(self) -> str:
        # The stable stamp written onto every chunk, e.g. "all-MiniLM-L6-v2@1".
        return f"{self._model_name}@{_MODEL_VERSION}"
