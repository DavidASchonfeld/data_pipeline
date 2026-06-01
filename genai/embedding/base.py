from abc import ABC, abstractmethod


# EmbedderError: one error type every embedder raises, so callers (the EPIC 7 ingest runner and the
# EPIC 8 retriever) catch a single exception regardless of which backend is active. Wraps the raw
# library/model failures with the model name for context — mirrors LLMProviderError in genai/llm.
class EmbedderError(Exception):
    pass


# Embedder: a contract (interface) that every embedding backend must fulfil.
# Adding a new backend means creating a new file and implementing this one method + two properties.


class Embedder(ABC):
    # embed_batch: turn a list of texts into a list of vectors — one vector per text, in the same order
    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    # dim: how many numbers are in each vector (must match the pgvector column width, 384 here)
    @property
    @abstractmethod
    def dim(self) -> int: ...

    # model_id: a stable "name@version" stamp written onto every chunk, so swapping the model later
    # re-embeds only the rows whose stamp no longer matches instead of rebuilding the whole index
    @property
    @abstractmethod
    def model_id(self) -> str: ...
