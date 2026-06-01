# shared fixtures for the local embedder tests — fakes sentence-transformers so tests never download
# the ~80 MB model or touch the network, and centralises the config/module reload boilerplate
import sys
from unittest.mock import MagicMock

import pytest


class _FakeArray:
    # stand-in for the numpy array model.encode() returns — only .tolist() is exercised by the embedder
    def __init__(self, rows):
        self._rows = rows

    def tolist(self):
        return self._rows


class FakeSentenceTransformer:
    # records construction + encode calls and returns deterministic 384-length vectors (no real model)
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.encode_calls = []

    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, texts, normalize_embeddings=False, convert_to_numpy=False):
        self.encode_calls.append({
            "texts": list(texts),
            "normalize_embeddings": normalize_embeddings,
            "convert_to_numpy": convert_to_numpy,
        })
        # one distinct 384-d vector per input text, in order
        rows = [[float(i)] * 384 for i, _ in enumerate(texts)]
        return _FakeArray(rows)


def _fake_module():
    # a fake "sentence_transformers" module whose SentenceTransformer constructor is a spy we can assert on
    fake = MagicMock()
    fake.SentenceTransformer = MagicMock(side_effect=FakeSentenceTransformer)
    return fake


@pytest.fixture
def st_env(monkeypatch):
    # install the fake SDK + embedding env for the whole test, then reload config + local_st so they
    # pick up the patched values; returns (reloaded local_st module, the SentenceTransformer spy)
    fake = _fake_module()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    import importlib
    import genai.config as cfg
    importlib.reload(cfg)
    import genai.embedding.local_st as local_st
    importlib.reload(local_st)
    return local_st, fake.SentenceTransformer
