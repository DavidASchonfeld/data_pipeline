from __future__ import annotations

import logging
import re
import time

from genai import config

# In-memory BM25 keyword index over the pgvector `chunks` table — the keyword half of EPIC 8's
# hybrid search (the meaning half is the vector query in hybrid_retriever.py).
#
# BM25 is the classic "how well do these exact words match?" ranking used by search engines. It runs
# in process over the ~hundreds of chunk texts, so it is rebuilt from the database and cached for an
# hour rather than queried live. The corpus is kept SEPARATE PER NAMESPACE so a filings search can
# never surface a weather chunk (and, after porting, never leaks one subject into another): namespace
# scoping is structural here, not a filter that could be forgotten.
#
# WHY deferred imports: rank_bm25 and psycopg2 are only pulled in inside functions so that importing
# this module (e.g. during Airflow DAG parsing) stays memory-light.

logger = logging.getLogger(__name__)

# Module-level cache: one built index, reused until it goes stale. time.monotonic() is used (not
# wall-clock) so the TTL is immune to system clock changes.
_index: "BM25Index | None" = None
_built_at: float = 0.0

# Split text into lowercase alphanumeric runs. Used identically when building the index (per chunk)
# and when scoring a query, so the two always tokenize the same way.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _load_rows() -> list[dict]:
    # Pull every chunk once (id, namespace, text, metadata) through the shared connection pool. The
    # table holds only a few hundred rows, so a single unfiltered SELECT is cheap and simple.
    from genai.retrieval.pgvector_client import connection  # deferred: psycopg2 is heavy

    rows: list[dict] = []
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, namespace, external_id, text, metadata FROM chunks")
        for id_, namespace, external_id, text, metadata in cur.fetchall():
            rows.append({
                "id": id_,
                "namespace": namespace,
                "external_id": external_id,
                "text": text,
                # psycopg2 returns a JSONB column as a dict already; guard against a None metadata.
                "metadata": metadata or {},
            })
    return rows


class BM25Index:
    """A BM25 keyword index split into one independent corpus per namespace.

    _by_ns maps a namespace to {"bm25": BM25Okapi, "rows": [row dict, ...]} where the rows list is
    index-aligned with the BM25 corpus, so a BM25 document position maps straight back to its row.
    """

    def __init__(self, rows: list[dict]):
        from rank_bm25 import BM25Okapi  # deferred: only load the lib when an index is actually built

        # Bucket the flat row list by namespace, preserving order so positions stay aligned.
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(row["namespace"], []).append(row)

        self._by_ns: dict[str, dict] = {}
        for namespace, ns_rows in buckets.items():
            corpus = [_tokenize(r["text"]) for r in ns_rows]
            # BM25Okapi needs a non-empty corpus; an all-empty namespace is skipped (nothing to match).
            if corpus:
                self._by_ns[namespace] = {"bm25": BM25Okapi(corpus), "rows": ns_rows}

        logger.info("BM25 index built: %s", {ns: len(v["rows"]) for ns, v in self._by_ns.items()})

    def _score_namespace(self, namespace: str, query_tokens: list[str]) -> list[tuple[dict, float]]:
        # Score one namespace's corpus and return (row, score) pairs, best first.
        entry = self._by_ns.get(namespace)
        if entry is None:
            return []
        scores = entry["bm25"].get_scores(query_tokens)
        paired = list(zip(entry["rows"], (float(s) for s in scores)))
        paired.sort(key=lambda t: t[1], reverse=True)
        return paired

    def search(self, query: str, namespace: str | None, candidate_pool: int, predicate) -> list[tuple[dict, int]]:
        """Return up to candidate_pool (row, rank) pairs, rank 1-based, best first.

        namespace selects which corpus to search (None = all corpora, merged by score — scores are not
        strictly comparable across corpora, but the result is rank-based for RRF and the vector arm
        keeps cross-namespace ranking consistent). predicate(row) -> bool drops rows that fail the
        metadata filters; it is applied AFTER scoring so each corpus's IDF statistics stay intact.
        """
        query_tokens = _tokenize(query)

        scored: list[tuple[dict, float]] = []
        if namespace is not None:
            scored = self._score_namespace(namespace, query_tokens)
        else:
            for ns in self._by_ns:
                scored.extend(self._score_namespace(ns, query_tokens))
            scored.sort(key=lambda t: t[1], reverse=True)

        # Keep only rows that pass the metadata filters, then take the top candidate_pool and number
        # them 1, 2, 3, … — that rank (not the raw score) is what RRF fuses.
        results: list[tuple[dict, int]] = []
        for row, _score in scored:
            if predicate(row):
                results.append((row, len(results) + 1))
                if len(results) >= candidate_pool:
                    break
        return results


def get_bm25_index(force_rebuild: bool = False) -> BM25Index:
    """Return the cached BM25 index, rebuilding it from pgvector when missing, stale, or forced."""
    global _index, _built_at
    stale = (time.monotonic() - _built_at) > config.GENAI_BM25_TTL_SECONDS
    if _index is None or stale or force_rebuild:
        start = time.monotonic()
        _index = BM25Index(_load_rows())
        _built_at = time.monotonic()
        logger.info("BM25 index (re)built in %.0f ms", (_built_at - start) * 1000)
    return _index
