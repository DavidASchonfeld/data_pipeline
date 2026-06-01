from __future__ import annotations

import logging

from genai import config
from genai.retrieval.bm25_index import get_bm25_index
from genai.retrieval.ingest import _vector_literal  # pure text-cast helper; reused, not duplicated
from genai.retrieval.types import Passage

# The single search choke point for the GenAI layer (EPIC 8).
#
# retrieve() is the ONLY code path that queries pgvector for similarity or builds a BM25 result set.
# Keeping it the sole entry point means namespace scoping (and any future security filter) is
# impossible to bypass: no other module issues a vector or keyword query directly.
#
# It runs two searches at once and blends them with Reciprocal Rank Fusion (RRF):
#   - vector arm  : cosine similarity over the chunk embeddings (finds passages by MEANING)
#   - keyword arm : BM25 over the chunk text (finds passages by exact WORDS)
# Each arm produces a ranked list; RRF rewards passages both arms agree on. See genai/config.py for
# the K / candidate-pool / RRF-constant / weight knobs.

logger = logging.getLogger(__name__)


# RetrieverError: the single error type this layer raises, so callers (the EPIC 9 orchestrator)
# catch one exception regardless of whether the database, the embedder, or BM25 was the cause.
# Mirrors LLMProviderError / EmbedderError elsewhere in genai/.
class RetrieverError(Exception):
    pass


def _build_filter(filters: dict | None):
    """Translate a filters dict into matching guards for BOTH search arms, from one source of truth.

    Returns (where_sql, params, predicate):
      - where_sql / params  : a parameterized SQL fragment for the vector query's WHERE clause.
      - predicate(row)->bool : the same conditions in Python, for filtering BM25 candidate rows.
    Filter semantics: the key "namespace" maps to the `namespace` column; every other key maps to a
    JSONB metadata match `metadata->>'key' = value`. Values are ALWAYS parameterized — never string
    -interpolated — so a crafted filter value can't inject SQL.
    """
    filters = filters or {}
    clauses: list[str] = []
    params: list = []

    namespace = filters.get("namespace")
    if namespace is not None:
        clauses.append("namespace = %s")
        params.append(namespace)

    meta_items = [(k, str(v)) for k, v in filters.items() if k != "namespace"]
    for key, value in meta_items:
        # %s for the key AND the value keeps both out of the SQL string itself.
        clauses.append("metadata->>%s = %s")
        params.append(key)
        params.append(value)

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    def predicate(row: dict) -> bool:
        if namespace is not None and row.get("namespace") != namespace:
            return False
        meta = row.get("metadata") or {}
        return all(str(meta.get(key)) == value for key, value in meta_items)

    return where_sql, params, predicate


def _embed_query(question: str) -> list[float]:
    # Turn the question into the same kind of 384-number vector the chunks were stored as. Deferred
    # import: sentence-transformers is heavy and only needed when a real search runs.
    from genai.embedding import get_embedder

    return get_embedder().embed_batch([question])[0]


def _vector_search(question: str, where_sql: str, where_params: list, candidate_pool: int) -> list[tuple[dict, int]]:
    # Meaning search: order chunks by cosine distance (<=>) to the query vector. The HNSW index uses
    # vector_cosine_ops and the embeddings are L2-normalized, so <=> is true cosine distance (smaller
    # = closer). Returns (row, rank) pairs, rank 1-based.
    from genai.retrieval.pgvector_client import connection  # deferred: psycopg2 is heavy

    literal = _vector_literal(_embed_query(question))
    sql = (
        "SELECT id, namespace, external_id, text, metadata "
        "FROM chunks" + where_sql + " ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    # Param order matches %s order in the text: WHERE params, then the vector (ORDER BY), then LIMIT.
    params = list(where_params) + [literal, candidate_pool]

    results: list[tuple[dict, int]] = []
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        for rank, (id_, namespace, external_id, text, metadata) in enumerate(cur.fetchall(), start=1):
            row = {"id": id_, "namespace": namespace, "external_id": external_id,
                   "text": text, "metadata": metadata or {}}
            results.append((row, rank))
    return results


def _fuse(vector_results: list[tuple[dict, int]], bm25_results: list[tuple[dict, int]], k: int) -> list[Passage]:
    # Reciprocal Rank Fusion: each arm contributes weight / (RRF_CONSTANT + rank) to a chunk's score,
    # so a chunk both arms rank highly wins. Ties break by id ascending for deterministic output.
    c = config.GENAI_RRF_CONSTANT
    scores: dict[int, float] = {}
    rows: dict[int, dict] = {}

    for row, rank in vector_results:
        scores[row["id"]] = scores.get(row["id"], 0.0) + config.GENAI_RRF_VECTOR_WEIGHT / (c + rank)
        rows[row["id"]] = row
    for row, rank in bm25_results:
        scores[row["id"]] = scores.get(row["id"], 0.0) + config.GENAI_RRF_BM25_WEIGHT / (c + rank)
        rows[row["id"]] = row

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    passages: list[Passage] = []
    for id_, score in ordered[:k]:
        row = rows[id_]
        meta = row["metadata"]
        passages.append(Passage(id=id_, text=row["text"], source=meta.get("source", ""),
                                score=score, metadata=meta))
    return passages


def _maybe_rerank(passages: list[Passage], question: str) -> list[Passage]:
    # Optional re-ranking seam. OFF by default — returns the fused list unchanged.
    #
    # TODO (post-EPIC 11): once the eval harness can measure recall@k / faithfulness, enable a
    # re-ranking pass here behind GENAI_RERANK_ENABLED — re-score the fused candidates and re-order
    # them. Preferred is a cross-encoder reranker if the node has RAM headroom; otherwise fall back to
    # an LLM-as-reranker reusing get_llm_provider(). Build it only with measured evidence that it helps.
    if not config.GENAI_RERANK_ENABLED:
        return passages
    return passages


def retrieve(question: str, k: int = 5, filters: dict | None = None) -> list[Passage]:
    """Return the top-k passages for a question, fusing keyword (BM25) and meaning (vector) search.

    filters scopes BOTH arms identically, e.g. {"namespace": "sec_filings", "ticker": "AAPL"}. This is
    the only function that searches pgvector or BM25 — namespace scoping can't be bypassed.
    """
    if not isinstance(question, str) or not question.strip():
        raise RetrieverError("retrieve() requires a non-empty question string")
    if k < 1:
        raise RetrieverError(f"retrieve() requires k >= 1, got {k}")

    namespace = (filters or {}).get("namespace")
    candidate_pool = config.GENAI_RETRIEVAL_CANDIDATE_POOL

    try:
        where_sql, where_params, predicate = _build_filter(filters)
        vector_results = _vector_search(question, where_sql, where_params, candidate_pool)
        bm25_results = get_bm25_index().search(question, namespace, candidate_pool, predicate)
        passages = _fuse(vector_results, bm25_results, k)
        passages = _maybe_rerank(passages, question)
    except RetrieverError:
        raise
    except Exception as exc:
        # Log filters + k only — never the question text or passage contents (no PII in logs).
        logger.error("retrieve failed (k=%s, filters=%s): %s", k, filters, exc)
        raise RetrieverError(f"retrieve failed for filters={filters!r}: {exc}") from exc

    logger.info("retrieve ok: k=%s filters=%s vector=%d bm25=%d returned=%d",
                k, filters, len(vector_results), len(bm25_results), len(passages))
    return passages
