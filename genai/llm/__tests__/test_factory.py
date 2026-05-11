"""Offline tests for get_llm_provider() — these run without an API key or network."""
import os

import pytest

# temporarily override env vars during tests without touching the real environment
from unittest.mock import patch


def test_unknown_provider_raises_value_error():
    # confirm that asking for an unsupported provider gives a clear, descriptive error
    with patch.dict(os.environ, {"LLM_PROVIDER": "bogus-provider"}):
        # reload config so the patched env var takes effect
        import importlib
        import genai.config as cfg
        importlib.reload(cfg)
        import genai.llm._factory as factory
        importlib.reload(factory)

        with pytest.raises(ValueError, match="bogus-provider"):
            factory.get_llm_provider()


def test_anthropic_provider_returned_for_anthropic_key():
    # confirm that LLM_PROVIDER=anthropic returns an AnthropicProvider instance (no real API call made)
    # fake the anthropic SDK in sys.modules so the test runs even when the package is not installed locally
    import sys
    from unittest.mock import MagicMock

    fake_anthropic = MagicMock()
    fake_anthropic.Anthropic.return_value = MagicMock()  # fake client object

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "sk-fake-key-for-testing"}):
            import importlib
            import genai.config as cfg
            importlib.reload(cfg)
            import genai.llm._factory as factory
            importlib.reload(factory)
            import genai.llm.anthropic_provider as ap_module
            importlib.reload(ap_module)

            from genai.llm.anthropic_provider import AnthropicProvider
            provider = factory.get_llm_provider()
            assert isinstance(provider, AnthropicProvider)
