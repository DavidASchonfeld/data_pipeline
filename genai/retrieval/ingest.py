from __future__ import annotations

import hashlib
import logging
import re

from genai.retrieval.chunker import chunk
from genai.retrieval.types import Chunk

# Turns Snowflake source rows into pgvector chunk rows, then upserts them — embedding only what
# actually changed and deleting chunks whose source row disappeared.
#
# This module is pure orchestration over the chunker, the embedder, and a pgvector connection: the
# runner (genai/runners/ingest_runner.py) supplies the rows + connection and owns the transaction.

logger = logging.getLogger(__name__)

# Namespace values are the subject boundary in the shared `chunks` table (ADR 0005). They MUST match
# the Done-when criteria and the EPIC 8 retriever's namespace filter.
NAMESPACE_FILINGS = "sec_filings"
NAMESPACE_WEATHER = "weather"

_ITEM_CODE = re.compile(r"item\s+(\d{1,2}[a-z]?)", re.IGNORECASE)


def _section_slug(section: str) -> str:
    # Compact, stable token for a section name used inside external_id, e.g. "Item 1A - Risk Factors"
    # -> "item1a", "full" -> "full". Keeps external_id short and human-readable.
    m = _ITEM_CODE.search(section or "")
    if m:
        return "item" + m.group(1).lower()
    return re.sub(r"[^a-z0-9]+", "_", (section or "section").lower()).strip("_") or "section"


def _iso(value) -> str:
    # Snowflake DATE columns come back as datetime.date; normalise to an ISO string for ids + metadata.
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _source_hash(text: str, embedder_model_id: str) -> str:
    # Fingerprint of (text + embedder id). Unchanged text + same embedder => unchanged hash => skip
    # re-embedding. Including the embedder id means swapping the model forces a re-embed (reference §2).
    h = hashlib.sha256()
    h.update(embedder_model_id.encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _stamp(text: str, embedder) -> dict:
    # The embedder + hash fields every chunk carries, regardless of source.
    return {
        "embedder_model_id": embedder.model_id,
        "embedder_dim": embedder.dim,
        "source_hash": _source_hash(text, embedder.model_id),
    }


def make_filing_chunks(section_rows: list[dict], embedder) -> list[Chunk]:
    """Render FCT_FILING_SECTIONS rows into recursive chunks. Rows: {ticker, filing_date, section, section_text}."""
    chunks: list[Chunk] = []
    for row in section_rows:
        ticker = row["ticker"]
        filing_date = _iso(row["filing_date"])
        section = row["section"]
        slug = _section_slug(section)
        for i, piece in enumerate(chunk(row["section_text"] or "", strategy="recursive")):
            meta = {"source": "sec_edgar", "ticker": ticker, "filing_date": filing_date,
                    "section": section, "chunk_index": i, **_stamp(piece, embedder)}
            chunks.append(Chunk(NAMESPACE_FILINGS, f"sec:{ticker}:{filing_date}:{slug}:{i}", piece, meta))
    return chunks


def make_weather_chunks(summary_rows: list[dict], embedder) -> list[Chunk]:
    """Render FCT_WEATHER_SUMMARIES rows into whole-document chunks. Rows: {city, week_start, summary_text}."""
    chunks: list[Chunk] = []
    for row in summary_rows:
        city = row["city"]
        week_start = _iso(row["week_start"])
        for piece in chunk(row["summary_text"] or "", strategy="whole"):
            meta = {"source": "open_meteo", "city": city, "week_start": week_start, **_stamp(piece, embedder)}
            chunks.append(Chunk(NAMESPACE_WEATHER, f"weather:{city}:{week_start}", piece, meta))
    return chunks


def _vector_literal(vector: list[float]) -> str:
    # pgvector accepts a text literal like "[0.1,0.2,...]" cast to ::vector — avoids needing the
    # numpy/register_vector adapter just to INSERT.
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def upsert_chunks(pg_conn, namespace: str, chunks: list[Chunk], embedder, batch_size: int) -> dict:
    """Embed only changed/new chunks and upsert them. Returns {"upserted": n, "skipped": n}.

    Reads the stored source_hash per external_id; a chunk whose hash is unchanged is skipped entirely
    (no embed call, no write). Does NOT commit — the caller owns the transaction.
    """
    import psycopg2.extras

    cur = pg_conn.cursor()
    cur.execute("SELECT external_id, metadata->>'source_hash' FROM chunks WHERE namespace = %s", (namespace,))
    existing = dict(cur.fetchall())

    to_embed = [c for c in chunks if existing.get(c.external_id) != c.metadata["source_hash"]]
    skipped = len(chunks) - len(to_embed)
    if not to_embed:
        return {"upserted": 0, "skipped": skipped}

    # Embed in batches so the model never holds the whole corpus in memory at once.
    texts = [c.text for c in to_embed]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed_batch(texts[i : i + batch_size]))

    upsert_sql = """
        INSERT INTO chunks (namespace, external_id, text, embedding, metadata)
        VALUES (%s, %s, %s, %s::vector, %s)
        ON CONFLICT (namespace, external_id)
        DO UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata
    """
    for c, vec in zip(to_embed, vectors):
        cur.execute(upsert_sql, (c.namespace, c.external_id, c.text, _vector_literal(vec),
                                 psycopg2.extras.Json(c.metadata)))
    return {"upserted": len(to_embed), "skipped": skipped}


def delete_orphans(pg_conn, namespace: str, seen_external_ids: list[str]) -> int:
    """Delete chunks in the namespace whose external_id was NOT seen this run. Returns rows deleted.

    Removes vectors whose source row was deleted or regenerated under a new id, so retrieval never
    answers from orphaned chunks (reference §1). Does NOT commit — the caller owns the transaction.
    """
    cur = pg_conn.cursor()
    if not seen_external_ids:
        # No source rows at all this run — every chunk in the namespace is an orphan. (Avoids the
        # "cannot determine type of empty array" Postgres raises for <> ALL on an empty list.)
        cur.execute("DELETE FROM chunks WHERE namespace = %s", (namespace,))
    else:
        # <> ALL(array) keeps only ids present this run; the rest (deleted/regenerated rows) are removed.
        cur.execute("DELETE FROM chunks WHERE namespace = %s AND external_id <> ALL(%s)", (namespace, seen_external_ids))
    return cur.rowcount
