# retrieval: tools for finding the most relevant text — the pgvector client, chunker, ingest, and
# (EPIC 8) the hybrid retriever live here. Import the public retrieval API from here, never from the
# concrete modules, so callers (the EPIC 9 orchestrator) depend on one stable entry point.
from genai.retrieval.hybrid_retriever import RetrieverError, retrieve
from genai.retrieval.types import Passage

__all__ = ["retrieve", "Passage", "RetrieverError"]
