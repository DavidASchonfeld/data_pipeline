"""Offline tests for get_embedder() — these run without a model download or network."""
import os
from unittest.mock import patch

import pytest


def test_unknown_provider_raises_value_error():
    # confirm that asking for an unsupported embedder gives a clear, descriptive error
    with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "bogus-embedder"}):
        # reload config so the patched env var takes effect, then reload the factory that reads it
        import importlib
        import genai.config as cfg
        importlib.reload(cfg)
        import genai.embedding._factory as factory
        importlib.reload(factory)

        with pytest.raises(ValueError, match="bogus-embedder"):
            factory.get_embedder()


def test_local_embedder_returned_for_local_provider():
    # confirm that EMBEDDING_PROVIDER=local returns a LocalSentenceTransformerEmbedder (no model loaded —
    # construction is lazy, so this needs no fake SDK)
    with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "local"}):
        import importlib
        import genai.config as cfg
        importlib.reload(cfg)
        import genai.embedding._factory as factory
        importlib.reload(factory)

        from genai.embedding.local_st import LocalSentenceTransformerEmbedder
        assert isinstance(factory.get_embedder(), LocalSentenceTransformerEmbedder)
