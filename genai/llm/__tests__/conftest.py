# shared fixtures for LLM provider tests — centralises provider construction so each test file
# only contains the actual test logic, not boilerplate setup
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


def _make_openai_provider():
    # build an OpenAIProvider with a fake SDK and fake API key — never touches the network
    fake_openai = MagicMock()
    fake_client = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "LLM_API_KEY": "sk-test", "LLM_MODEL": "gpt-4o"}):
            import importlib
            import genai.config as cfg
            importlib.reload(cfg)
            import genai.llm.openai_provider as op
            importlib.reload(op)
            provider = op.OpenAIProvider()
            provider._client = fake_client  # swap in the fake client so tests control all API calls
    return provider, fake_client


def _make_anthropic_provider():
    # build an AnthropicProvider with a fake SDK and fake API key — never touches the network
    fake_anthropic = MagicMock()
    fake_client = MagicMock()
    fake_anthropic.Anthropic.return_value = fake_client
    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "sk-test", "LLM_MODEL": "claude-sonnet-4-6"}):
            import importlib
            import genai.config as cfg
            importlib.reload(cfg)
            import genai.llm.anthropic_provider as ap
            importlib.reload(ap)
            provider = ap.AnthropicProvider()
            provider._client = fake_client  # swap in the fake client so tests control all API calls
    return provider, fake_client


@pytest.fixture
def openai_prov():
    return _make_openai_provider()


@pytest.fixture
def anthropic_prov():
    return _make_anthropic_provider()
