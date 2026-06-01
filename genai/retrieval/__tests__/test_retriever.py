"""Offline unit tests for the EPIC 8 hybrid retriever — pgvector, the embedder, and rank_bm25 are all
faked, so nothing touches a real database, model, or network."""
import contextlib
import sys
import types
from unittest.mock import MagicMock

import pytest

from genai import config
from genai.retrieval import bm25_index
from genai.retrieval import hybrid_retriever as hr
from genai.retrieval.hybrid_retriever import RetrieverError, _build_filter, _fuse
from genai.retrieval.types import Passage


def _row(i: int, namespace: str = "sec_filings", text: str = "", metadata: dict | None = None) -> dict:
    # Build a chunk row dict in the shape both search arms produce.
    return {"id": i, "namespace": namespace, "external_id": str(i),
            "text": text or f"text {i}", "metadata": metadata or {"source": "sec_edgar"}}


class _FakeBM25Okapi:
    # Deterministic BM25 stand-in: a document's score is how many distinct query tokens it contains.
    def __init__(self, corpus):
        self.corpus = corpus

    def get_scores(self, query_tokens):
        q = set(query_tokens)
        return [float(len(q & set(doc))) for doc in self.corpus]


@pytest.fixture
def fake_rank_bm25(monkeypatch):
    # Install the fake rank_bm25 so BM25Index can be built without the real (heavy) library.
    mod = types.ModuleType("rank_bm25")
    mod.BM25Okapi = _FakeBM25Okapi
    monkeypatch.setitem(sys.modules, "rank_bm25", mod)
    return mod


# ── Passage dataclass ─────────────────────────────────────────────────────────


def test_passage_fields_and_default_metadata():
    p = Passage(id=7, text="hello", source="sec_edgar", score=0.5)
    assert (p.id, p.text, p.source, p.score) == (7, "hello", "sec_edgar", 0.5)
    assert p.metadata == {}


# ── _build_filter: SQL fragment + Python predicate from one source ──────────────


def test_build_filter_namespace_and_metadata_parameterized():
    where, params, predicate = _build_filter({"namespace": "sec_filings", "ticker": "AAPL"})
    assert "namespace = %s" in where
    assert "metadata->>%s = %s" in where
    # Both the key and value are bound parameters — never interpolated into the SQL string.
    assert "AAPL" not in where and "ticker" not in where
    assert params[0] == "sec_filings"
    assert "ticker" in params and "AAPL" in params
    assert predicate({"namespace": "sec_filings", "metadata": {"ticker": "AAPL"}}) is True
    assert predicate({"namespace": "sec_filings", "metadata": {"ticker": "GOOGL"}}) is False
    assert predicate({"namespace": "weather", "metadata": {"ticker": "AAPL"}}) is False


def test_build_filter_empty_matches_everything():
    where, params, predicate = _build_filter(None)
    assert where == "" and params == []
    assert predicate({"namespace": "anything", "metadata": {}}) is True


# ── RRF fusion ─────────────────────────────────────────────────────────────────


def test_rrf_rewards_agreement_and_scores_exactly():
    # id 2 appears in BOTH arms, so it should rank first with the summed contribution.
    vector = [(_row(1), 1), (_row(2), 2)]
    bm25 = [(_row(2), 1), (_row(3), 2)]
    result = _fuse(vector, bm25, k=3)
    assert result[0].id == 2
    c = config.GENAI_RRF_CONSTANT
    expected = config.GENAI_RRF_VECTOR_WEIGHT / (c + 2) + config.GENAI_RRF_BM25_WEIGHT / (c + 1)
    assert abs(result[0].score - expected) == pytest.approx(0.0, abs=1e-12)
    assert result[0].source == "sec_edgar"


def test_rrf_tiebreak_is_id_ascending():
    # Equal scores (same rank in different arms) resolve by id ascending for deterministic output.
    result = _fuse([(_row(5), 1)], [(_row(3), 1)], k=2)
    assert [p.id for p in result] == [3, 5]


# ── BM25 index: namespace isolation, metadata filter, TTL ───────────────────────


def test_bm25_namespace_isolation(fake_rank_bm25):
    rows = [
        _row(1, "sec_filings", "apple risk factors"),
        _row(2, "weather", "apple weather sunny"),
    ]
    idx = bm25_index.BM25Index(rows)
    results = idx.search("apple", namespace="sec_filings", candidate_pool=10, predicate=lambda r: True)
    assert results and all(r["namespace"] == "sec_filings" for r, _ in results)


def test_bm25_metadata_filter_drops_nonmatching(fake_rank_bm25):
    rows = [
        _row(1, "sec_filings", "apple risk", {"source": "sec_edgar", "ticker": "AAPL"}),
        _row(2, "sec_filings", "google risk", {"source": "sec_edgar", "ticker": "GOOGL"}),
    ]
    idx = bm25_index.BM25Index(rows)
    only_aapl = lambda r: str(r["metadata"].get("ticker")) == "AAPL"
    results = idx.search("risk", "sec_filings", 10, only_aapl)
    assert [r["id"] for r, _ in results] == [1]
    # Ranks are 1-based over the filtered set.
    assert results[0][1] == 1


def test_bm25_ttl_rebuild_then_reuse(fake_rank_bm25, monkeypatch):
    calls = {"n": 0}

    def fake_load():
        calls["n"] += 1
        return [_row(1, "sec_filings", "apple risk")]

    class _Clock:
        t = 1000.0

        def monotonic(self):
            return self.t

    clock = _Clock()
    monkeypatch.setattr(bm25_index, "_load_rows", fake_load)
    monkeypatch.setattr(bm25_index, "time", clock)
    monkeypatch.setattr(bm25_index, "_index", None)
    monkeypatch.setattr(bm25_index, "_built_at", 0.0)

    bm25_index.get_bm25_index()            # first call builds
    assert calls["n"] == 1
    bm25_index.get_bm25_index()            # still fresh — reused
    assert calls["n"] == 1
    clock.t = 1000.0 + config.GENAI_BM25_TTL_SECONDS + 1  # past the TTL — rebuilt
    bm25_index.get_bm25_index()
    assert calls["n"] == 2


# ── _vector_search: SQL shape + parameter order ─────────────────────────────────


def test_vector_search_sql_is_parameterized_and_limited(monkeypatch):
    monkeypatch.setattr(hr, "_embed_query", lambda q: [0.1] * 384)
    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    @contextlib.contextmanager
    def _conn():
        conn = MagicMock()
        conn.cursor.return_value = _Cur()
        yield conn

    monkeypatch.setattr("genai.retrieval.pgvector_client.connection", _conn)

    hr._vector_search("apple risk", " WHERE namespace = %s", ["sec_filings"], 20)
    sql = captured["sql"]
    assert "<=>" in sql and "::vector" in sql and "LIMIT %s" in sql
    assert "WHERE namespace = %s" in sql
    # Param order: WHERE params, then the query vector literal (ORDER BY), then the LIMIT.
    assert captured["params"][0] == "sec_filings"
    assert captured["params"][-1] == 20
    assert captured["params"][-2].startswith("[") and captured["params"][-2].endswith("]")


# ── retrieve(): end-to-end flow + validation + error wrapping ───────────────────


def test_retrieve_returns_k_passages_sorted(monkeypatch):
    monkeypatch.setattr(hr, "_vector_search", lambda q, w, p, cp: [(_row(1), 1), (_row(2), 2)])
    fake_idx = MagicMock()
    fake_idx.search.return_value = [(_row(2), 1), (_row(3), 2)]
    monkeypatch.setattr(hr, "get_bm25_index", lambda: fake_idx)

    result = hr.retrieve("apple risk", k=2)
    assert len(result) == 2
    assert all(isinstance(p, Passage) for p in result)
    assert result[0].score >= result[1].score
    assert result[0].id == 2  # agreed by both arms


def test_retrieve_empty_question_raises():
    with pytest.raises(RetrieverError):
        hr.retrieve("")
    with pytest.raises(RetrieverError):
        hr.retrieve("   ")


def test_retrieve_bad_k_raises():
    with pytest.raises(RetrieverError):
        hr.retrieve("apple", k=0)


def test_retrieve_wraps_backend_failure(monkeypatch):
    def boom(*a, **k):
        raise ValueError("db unreachable")

    monkeypatch.setattr(hr, "_vector_search", boom)
    monkeypatch.setattr(hr, "get_bm25_index", lambda: MagicMock())
    with pytest.raises(RetrieverError):
        hr.retrieve("apple risk")
