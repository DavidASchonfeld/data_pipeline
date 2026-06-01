"""Offline unit tests for the streaming embed runner — fake embedder + in-memory streams, no model."""
import io
import json

import pytest

import genai.runners.embed_runner as er

pytestmark = pytest.mark.offline


class FakeEmbedder:
    # records each batch it was handed and returns deterministic 384-length vectors
    dim = 384
    model_id = "all-MiniLM-L6-v2@1"

    def __init__(self):
        self.batches = []

    def embed_batch(self, texts):
        self.batches.append(list(texts))
        return [[float(i)] * 384 for i, _ in enumerate(texts)]


@pytest.fixture
def fake_embedder(monkeypatch):
    # the runner does `from genai.embedding import get_embedder` at call time — patch that attribute
    fake = FakeEmbedder()
    monkeypatch.setattr("genai.embedding.get_embedder", lambda: fake)
    return fake


def _run(text):
    out = io.StringIO()
    rc = er.run(io.StringIO(text), out)
    return rc, out.getvalue().splitlines()


def test_single_line_emits_one_vector_line(fake_embedder):
    # the done-when `echo '{"id":"a","text":"hello world"}' | ...` example, run offline
    rc, lines = _run('{"id":"a","text":"hello world"}\n')
    assert rc == 0
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["id"] == "a"
    assert len(obj["vector"]) == 384


def test_streaming_preserves_order_and_count(fake_embedder):
    # 70 input lines → 70 output lines, ids echoed back in input order
    text = "\n".join(json.dumps({"id": i, "text": f"t{i}"}) for i in range(70)) + "\n"
    rc, lines = _run(text)
    assert rc == 0
    ids = [json.loads(line)["id"] for line in lines]
    assert ids == list(range(70))


def test_batching_bounds_buffer(fake_embedder):
    # with batch size 32, 70 lines flush as 32 + 32 + 6 — proving it never accumulates all 70 at once
    text = "\n".join(json.dumps({"id": i, "text": f"t{i}"}) for i in range(70)) + "\n"
    _run(text)
    assert [len(b) for b in fake_embedder.batches] == [32, 32, 6]


def test_malformed_line_is_skipped_not_fatal(fake_embedder):
    # a bad line between two good ones is dropped; the run still completes with the two valid vectors
    text = '{"id":"a","text":"x"}\nnot json at all\n{"id":"b","text":"y"}\n'
    rc, lines = _run(text)
    assert rc == 0
    assert [json.loads(line)["id"] for line in lines] == ["a", "b"]


def test_blank_lines_ignored(fake_embedder):
    # stray blank lines (e.g. a trailing newline) are skipped, not treated as malformed input
    text = '\n{"id":"a","text":"x"}\n\n{"id":"b","text":"y"}\n\n'
    rc, lines = _run(text)
    assert rc == 0
    assert len(lines) == 2
