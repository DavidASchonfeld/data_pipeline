"""Offline tests for the local sentence-transformers embedder — the SDK is faked (see conftest.py),
so nothing downloads the model or hits the network. One live test (real model) is marked `live`."""
import sys

import pytest


def test_dim_is_384_without_loading_model(st_env):
    # dim is a constant the caller can read up front — it must NOT trigger a model load
    local_st, ctor = st_env
    embedder = local_st.LocalSentenceTransformerEmbedder()
    assert embedder.dim == 384
    ctor.assert_not_called()  # lazy: no SentenceTransformer constructed just to ask for the dimension


def test_model_id_is_name_at_version(st_env):
    # the stable stamp written onto every chunk
    local_st, _ = st_env
    embedder = local_st.LocalSentenceTransformerEmbedder()
    assert embedder.model_id == "all-MiniLM-L6-v2@1"


def test_embed_batch_lazy_loads_model_only_once(st_env):
    # the model is built on first embed and cached — a second batch reuses it, not a fresh load
    local_st, ctor = st_env
    embedder = local_st.LocalSentenceTransformerEmbedder()
    embedder.embed_batch(["first"])
    embedder.embed_batch(["second"])
    assert ctor.call_count == 1


def test_embed_batch_shape_and_normalization(st_env):
    # one 384-length vector per input text, and normalization is requested (cosine search downstream)
    local_st, _ = st_env
    embedder = local_st.LocalSentenceTransformerEmbedder()
    vectors = embedder.embed_batch(["alpha", "beta"])
    assert isinstance(vectors, list) and len(vectors) == 2
    assert all(len(v) == 384 for v in vectors)
    # the underlying model was asked to L2-normalize the embeddings
    assert embedder._model.encode_calls[0]["normalize_embeddings"] is True


def test_embed_batch_empty_returns_empty_without_loading(st_env):
    # an empty batch short-circuits to [] and never loads the model
    local_st, ctor = st_env
    embedder = local_st.LocalSentenceTransformerEmbedder()
    assert embedder.embed_batch([]) == []
    ctor.assert_not_called()


def test_missing_library_raises_embedder_error(monkeypatch):
    # if sentence-transformers isn't importable, embed_batch fails loud with the layer's one error type
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # None → import raises ImportError
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    import importlib
    import genai.config as cfg
    importlib.reload(cfg)
    import genai.embedding.local_st as local_st
    importlib.reload(local_st)
    from genai.embedding.base import EmbedderError

    embedder = local_st.LocalSentenceTransformerEmbedder()
    with pytest.raises(EmbedderError, match="sentence-transformers"):
        embedder.embed_batch(["x"])


@pytest.mark.live
def test_real_embedding_returns_384_floats():
    # real end-to-end check — downloads/loads all-MiniLM-L6-v2 and embeds one string.
    # Marked `live` so CI (`pytest -m "not live"`) never pulls the ~80 MB model.
    from genai.embedding import get_embedder

    embedder = get_embedder()
    assert embedder.dim == 384
    assert embedder.model_id == "all-MiniLM-L6-v2@1"
    vectors = embedder.embed_batch(["hello world"])
    assert len(vectors) == 1 and len(vectors[0]) == 384
    assert all(isinstance(x, float) for x in vectors[0])
