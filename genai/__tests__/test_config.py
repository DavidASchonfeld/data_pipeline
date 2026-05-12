"""Tests for genai/config.py — run offline, no API key needed."""
import importlib
import os
from unittest.mock import patch

import pytest


def test_genai_enabled_defaults_to_false():
    # confirm the AI layer is off by default — the pipeline must work without it
    # patch load_dotenv so .env can't re-add the var we deliberately removed
    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
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
    # patch load_dotenv so .env can't re-add the var we deliberately removed
    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_PROVIDER", None)
        import genai.config as cfg
        importlib.reload(cfg)
        assert cfg.LLM_PROVIDER == "anthropic"


@pytest.mark.offline
def test_configured_provider_is_deployment_ready():
    # always runs — validates that whatever provider is set has all required credentials
    import genai.config as cfg
    importlib.reload(cfg)

    # cfg.__file__ always resolves to the actual config file path — stays correct if the code moves
    _cfg = cfg.__file__
    VALID_PROVIDERS = {"anthropic", "openai"}

    assert cfg.LLM_PROVIDER in VALID_PROVIDERS, (
        f"LLM_PROVIDER is '{cfg.LLM_PROVIDER}' — must be one of {sorted(VALID_PROVIDERS)}.\n"
        f"Set LLM_PROVIDER in the environment — see {_cfg} for deployment notes."
    )

    if cfg.LLM_PROVIDER == "openai":
        assert cfg.LLM_API_KEY, (
            f"LLM_API_KEY is not set.\n"
            f"Add your OpenAI API key to the environment — see {_cfg} for deployment notes."
        )
        assert not cfg.LLM_MODEL.startswith("claude"), (
            f"LLM_MODEL is '{cfg.LLM_MODEL}' — this looks like an Anthropic model name.\n"
            f"Set LLM_MODEL to an OpenAI model (e.g. gpt-4o) — see {_cfg} for deployment notes."
        )


def _assert_live_api_vars():
    # shared pre-check — called by both the var-check test and the live-connection test
    import genai.config as cfg
    importlib.reload(cfg)

    VALID_PROVIDERS = {"anthropic", "openai"}

    assert cfg.LLM_PROVIDER in VALID_PROVIDERS, (
        f"LLM_PROVIDER is '{cfg.LLM_PROVIDER}' — must be one of {sorted(VALID_PROVIDERS)}. "
        f"Set it in the environment before running live tests."
    )
    assert cfg.LLM_API_KEY, (
        "LLM_API_KEY is not set.\n"
        "Local dev: add to data_pipeline/.env:\n"
        '  LLM_API_KEY="<your-key>"\n'
        '  LLM_PROVIDER="anthropic"  # or: openai\n'
        "Deployed: set it in infra/genai/secrets/genai-secrets.yaml and redeploy.\n"
        "Then re-run: pytest -m live  (or: make test-all)"
    )
    assert cfg.LLM_MODEL, (
        "LLM_MODEL is not set.\n"
        "Local dev: add to data_pipeline/.env:\n"
        '  LLM_MODEL="claude-sonnet-4-6"  # or your chosen model name\n'
        "Deployed: set it in .env.deploy and redeploy.\n"
        "Then re-run: pytest -m live  (or: make test-all)"
    )


@pytest.mark.offline
def test_live_api_vars_configured():
    # confirms LLM_PROVIDER, LLM_API_KEY, and LLM_MODEL are all set — runs on deploy so a missing value fails fast
    import genai.config as cfg
    importlib.reload(cfg)
    if not cfg.GENAI_ENABLED:
        pytest.skip("GENAI_ENABLED is false — skipping API variable check")
    _assert_live_api_vars()


@pytest.mark.live
def test_configured_provider_live_connection():
    # makes a real API call — run manually with: pytest -m live
    # verifies credentials + the API accepts the chosen model
    from genai.llm._factory import get_llm_provider

    _assert_live_api_vars()

    import genai.config as cfg
    importlib.reload(cfg)
    provider = get_llm_provider()

    # test complete() — single-prompt path
    try:
        complete_response = provider.complete("Say hello in one word.", max_tokens=20)
    except Exception as exc:
        pytest.fail(
            f"Live complete() to '{cfg.LLM_PROVIDER}' failed: {exc}\n"
            f"Check that LLM_API_KEY and LLM_MODEL are correct in your environment."
        )
    assert isinstance(complete_response, str) and len(complete_response.strip()) > 0, (
        f"Provider '{cfg.LLM_PROVIDER}' returned an empty string from complete()."
    )

    # test chat() — multi-turn path, checks the normalised response shape
    try:
        chat_response = provider.chat(
            messages=[{"role": "user", "content": "What is 2 + 2?"}], max_tokens=20
        )
    except Exception as exc:
        pytest.fail(
            f"Live chat() to '{cfg.LLM_PROVIDER}' failed: {exc}"
        )
    for key in ("content", "stop_reason", "usage", "tool_calls"):
        assert key in chat_response, f"chat() response missing '{key}' key"
    assert isinstance(chat_response["content"], str) and len(chat_response["content"]) > 0, (
        f"Provider '{cfg.LLM_PROVIDER}' returned an empty string from chat()."
    )
