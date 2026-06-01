from __future__ import annotations

from dataclasses import dataclass, field

# Shared lightweight data types for the retrieval layer.
#
# Chunk is what EPIC 7 writes INTO pgvector (one row of the `chunks` table before embedding).
# Passage (EPIC 8) is what the retriever reads BACK OUT — added when the hybrid retriever lands.


@dataclass
class Chunk:
    """One unit of text to store in pgvector: maps directly onto a `chunks` table row.

    namespace + external_id are the UNIQUE upsert key (external_id is source-qualified, e.g.
    "sec:AAPL:2023-11-01:item1a:3"). metadata carries source-specific fields plus the embedder
    stamp + source_hash that drive re-embed-only-when-changed.
    """

    namespace: str
    external_id: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class Passage:
    """One retrieval result read BACK OUT of pgvector by EPIC 8's hybrid retriever.

    id maps to chunks.id (the row's BIGSERIAL primary key). source is a human-readable origin
    (e.g. "sec_edgar" / "open_meteo"), copied from metadata["source"]. score is the fused RRF score
    (higher = more relevant). metadata is the row's JSONB blob verbatim, so callers can read
    ticker/section/city/etc. and the EPIC 10 UI can attribute each passage to its source.
    """

    id: int
    text: str
    source: str
    score: float
    metadata: dict = field(default_factory=dict)
