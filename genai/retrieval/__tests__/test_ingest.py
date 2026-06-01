"""Offline unit tests for the RAG ingest helpers — embedder + psycopg2 are faked, no DB is touched."""
import sys
from unittest.mock import MagicMock

import pytest

from genai.retrieval import ingest
from genai.retrieval.ingest import (
    _section_slug,
    _source_hash,
    delete_orphans,
    make_filing_chunks,
    make_weather_chunks,
    upsert_chunks,
)


class FakeEmbedder:
    # Minimal Embedder stand-in: stable id/dim and a deterministic embed_batch.
    model_id = "test-model@1"
    dim = 384

    def embed_batch(self, texts):
        return [[0.0] * self.dim for _ in texts]


# ── slug + hash ─────────────────────────────────────────────────────────────


def test_section_slug_maps_item_codes():
    assert _section_slug("Item 1A - Risk Factors") == "item1a"
    assert _section_slug("Item 7 - Management Discussion and Analysis") == "item7"
    assert _section_slug("full") == "full"


def test_source_hash_changes_with_model_id():
    # Same text but a different embedder id must produce a different hash (forces re-embed on swap).
    assert _source_hash("hello", "model@1") != _source_hash("hello", "model@2")
    assert _source_hash("hello", "model@1") == _source_hash("hello", "model@1")


# ── chunk construction ────────────────────────────────────────────────────────


def test_make_filing_chunks_shape():
    rows = [{"ticker": "AAPL", "filing_date": "2023-11-01", "section": "Item 1A - Risk Factors",
             "section_text": "A short risk paragraph."}]
    chunks = make_filing_chunks(rows, FakeEmbedder())
    assert len(chunks) == 1
    c = chunks[0]
    assert c.namespace == ingest.NAMESPACE_FILINGS
    assert c.external_id == "sec:AAPL:2023-11-01:item1a:0"
    assert c.metadata["source"] == "sec_edgar"
    assert c.metadata["ticker"] == "AAPL"
    assert c.metadata["chunk_index"] == 0
    assert c.metadata["embedder_model_id"] == "test-model@1"
    assert c.metadata["embedder_dim"] == 384
    assert "source_hash" in c.metadata


def test_make_weather_chunks_shape():
    rows = [{"city": "Boston", "week_start": "2026-05-25", "summary_text": "Mild and clear all week."}]
    chunks = make_weather_chunks(rows, FakeEmbedder())
    assert len(chunks) == 1
    c = chunks[0]
    assert c.namespace == ingest.NAMESPACE_WEATHER
    assert c.external_id == "weather:Boston:2026-05-25"
    assert c.metadata["source"] == "open_meteo"
    assert c.metadata["city"] == "Boston"
    assert "source_hash" in c.metadata


# ── upsert: hash-skip logic ────────────────────────────────────────────────────


def _fake_psycopg2():
    # Install a fake psycopg2.extras so upsert_chunks' `import psycopg2.extras` + Json() work offline.
    fake = MagicMock()
    sys.modules["psycopg2"] = fake
    sys.modules["psycopg2.extras"] = fake.extras
    return fake


def test_upsert_skips_unchanged_and_embeds_changed():
    _fake_psycopg2()
    from genai.retrieval.types import Chunk

    ns = ingest.NAMESPACE_FILINGS
    unchanged = Chunk(ns, "id1", "text1", {"source_hash": "HASH_A"})
    changed = Chunk(ns, "id2", "text2", {"source_hash": "HASH_B_NEW"})

    cur = MagicMock()
    # Existing row id1 has the same hash (skip); id2 is absent (embed).
    cur.fetchall.return_value = [("id1", "HASH_A")]
    conn = MagicMock()
    conn.cursor.return_value = cur

    embedder = FakeEmbedder()
    embedder.embed_batch = MagicMock(return_value=[[0.0] * 384])

    stats = upsert_chunks(conn, ns, [unchanged, changed], embedder, batch_size=32)

    assert stats == {"upserted": 1, "skipped": 1}
    embedder.embed_batch.assert_called_once_with(["text2"])
    # One INSERT executed (plus the initial SELECT) — the unchanged chunk is never written.
    insert_calls = [c for c in cur.execute.call_args_list if "INSERT INTO chunks" in c.args[0]]
    assert len(insert_calls) == 1


def test_upsert_all_unchanged_does_zero_embeds():
    _fake_psycopg2()
    from genai.retrieval.types import Chunk

    ns = ingest.NAMESPACE_WEATHER
    c = Chunk(ns, "weather:Boston:2026-05-25", "Mild week.", {"source_hash": "H"})
    cur = MagicMock()
    cur.fetchall.return_value = [("weather:Boston:2026-05-25", "H")]
    conn = MagicMock()
    conn.cursor.return_value = cur

    embedder = FakeEmbedder()
    embedder.embed_batch = MagicMock()

    stats = upsert_chunks(conn, ns, [c], embedder, batch_size=32)
    assert stats == {"upserted": 0, "skipped": 1}
    embedder.embed_batch.assert_not_called()


def test_delete_orphans_runs_scoped_delete():
    cur = MagicMock()
    cur.rowcount = 3
    conn = MagicMock()
    conn.cursor.return_value = cur

    deleted = delete_orphans(conn, "sec_filings", ["a", "b"])
    assert deleted == 3
    sql, params = cur.execute.call_args.args
    assert "DELETE FROM chunks" in sql and "<> ALL(%s)" in sql
    assert params == ("sec_filings", ["a", "b"])


def test_delete_orphans_empty_seen_clears_namespace():
    # No source rows this run => every chunk is an orphan; avoid the empty-array <> ALL pitfall.
    cur = MagicMock()
    cur.rowcount = 5
    conn = MagicMock()
    conn.cursor.return_value = cur

    deleted = delete_orphans(conn, "weather", [])
    assert deleted == 5
    sql, params = cur.execute.call_args.args
    assert "DELETE FROM chunks WHERE namespace = %s" in sql and "ALL" not in sql
    assert params == ("weather",)
