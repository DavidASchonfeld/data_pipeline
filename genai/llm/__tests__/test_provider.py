"""Live smoke test — requires a real API key. Run inside the scheduler pod after deploy."""
import os

import pytest

# skip the entire module when no API key is set — keeps CI green without secrets
pytestmark = pytest.mark.skipif(
    not os.environ.get("LLM_API_KEY"),
    reason="LLM_API_KEY not set — skipping live API smoke test",
)


def test_complete_returns_non_empty_string():
    # confirm the provider can send a real request and get a meaningful text response back
    from genai.llm import get_llm_provider

    provider = get_llm_provider()
    result = provider.complete("Say hello in one word.", max_tokens=20)

    assert isinstance(result, str), "expected a text string back from the provider"
    assert len(result.strip()) > 0, "response was empty — the API call may have failed"
    # print so the output is visible when running inside the scheduler pod
    print(f"\nSmoke test response: {result!r}")


def test_chat_returns_normalised_dict():
    # confirm the chat method returns the expected structure (content, stop_reason, usage, tool_calls)
    from genai.llm import get_llm_provider

    provider = get_llm_provider()
    result = provider.chat(messages=[{"role": "user", "content": "What is 2 + 2?"}], max_tokens=20)

    assert "content" in result, "normalised response is missing the 'content' key"
    assert "stop_reason" in result, "normalised response is missing the 'stop_reason' key"
    assert "usage" in result, "normalised response is missing the 'usage' key"
    assert "tool_calls" in result, "normalised response is missing the 'tool_calls' key"
    assert isinstance(result["content"], str) and len(result["content"]) > 0
    print(f"\nChat response: {result['content']!r}")
