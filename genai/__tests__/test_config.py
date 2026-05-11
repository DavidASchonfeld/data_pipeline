"""Tests for genai/config.py — run offline, no API key needed."""
import importlib
import os
from unittest.mock import patch


def test_genai_enabled_defaults_to_false():
    # confirm the AI layer is off by default — the pipeline must work without it
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GENAI_ENABLED", None)  # remove if present from a previous test
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.GENAI_ENABLED is False


def test_genai_enabled_true_lowercase():
    # confirm "true" (lowercase) turns the flag on — standard shell convention
    with patch.dict(os.environ, {"GENAI_ENABLED": "true"}):
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.GENAI_ENABLED is True


def test_genai_enabled_true_uppercase():
    # confirm "TRUE" (uppercase) also works — environment variables are case-insensitive here
    with patch.dict(os.environ, {"GENAI_ENABLED": "TRUE"}):
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.GENAI_ENABLED is True


def test_genai_enabled_false_string():
    # confirm "false" string correctly keeps the flag off
    with patch.dict(os.environ, {"GENAI_ENABLED": "false"}):
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.GENAI_ENABLED is False


def test_default_llm_provider_is_anthropic():
    # confirm the default provider is Anthropic when nothing is set
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_PROVIDER", None)
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.LLM_PROVIDER == "anthropic"
