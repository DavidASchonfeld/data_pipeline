"""Offline unit tests for OpenAIProvider — mock the HTTP client so no API calls are made."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from genai.llm.base import LLMProviderError


def _fake_completion(content="Hello", finish_reason="stop", tool_calls=None, prompt_tokens=10, completion_tokens=5):
    # build a minimal fake OpenAI response object that matches the shape _normalise_response expects
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def test_complete_returns_text(openai_prov):
    # confirm complete() extracts the text from the API response
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion(content="Hello")
    result = provider.complete("Say hello.", max_tokens=20)
    assert result == "Hello"
    client.chat.completions.create.assert_called_once()


def test_complete_passes_prompt_as_user_message(openai_prov):
    # confirm the prompt is sent as a single user message, not a system message
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion()
    provider.complete("Test prompt", max_tokens=10)
    call_kwargs = client.chat.completions.create.call_args[1]
    assert call_kwargs["messages"] == [{"role": "user", "content": "Test prompt"}]


def test_chat_returns_normalised_shape(openai_prov):
    # confirm chat() returns a dict with all four required keys
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion(content="4")
    result = provider.chat(messages=[{"role": "user", "content": "2+2?"}], max_tokens=20)
    assert "content" in result
    assert "stop_reason" in result
    assert "usage" in result
    assert "tool_calls" in result
    assert result["content"] == "4"
    assert result["stop_reason"] == "stop"
    assert result["usage"]["input_tokens"] == 10
    assert result["usage"]["output_tokens"] == 5
    assert result["tool_calls"] == []


def test_chat_prepends_system_message(openai_prov):
    # confirm the system param becomes the first message in the list sent to OpenAI
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion()
    provider.chat(messages=[{"role": "user", "content": "Hi"}], system="You are helpful.", max_tokens=20)
    messages_sent = client.chat.completions.create.call_args[1]["messages"]
    assert messages_sent[0] == {"role": "system", "content": "You are helpful."}
    assert messages_sent[1] == {"role": "user", "content": "Hi"}


def test_chat_translates_tools_to_openai_format(openai_prov):
    # confirm the generic tool spec is converted into OpenAI's function-calling format
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion()
    tools = [{"name": "get_weather", "description": "Get weather", "parameters": {"type": "object", "properties": {}}}]
    provider.chat(messages=[{"role": "user", "content": "Weather?"}], tools=tools, max_tokens=20)
    sent_tools = client.chat.completions.create.call_args[1]["tools"]
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == "get_weather"


def test_chat_parses_tool_calls_in_response(openai_prov):
    # confirm tool calls in the response are parsed from JSON strings into dicts
    tc = MagicMock()
    tc.type = "function"  # match the real SDK: function tool calls carry type="function"
    tc.function.name = "get_weather"
    tc.function.arguments = json.dumps({"city": "NYC"})
    tc.id = "call_abc"
    provider, client = openai_prov
    client.chat.completions.create.return_value = _fake_completion(content="", tool_calls=[tc])
    result = provider.chat(messages=[{"role": "user", "content": "Weather?"}], max_tokens=20)
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "get_weather"
    assert result["tool_calls"][0]["input"] == {"city": "NYC"}


def test_client_built_with_timeout_and_retries():
    # confirm the configured timeout and retry count are passed into the SDK client constructor
    fake_openai = MagicMock()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with patch.dict(os.environ, {
            "LLM_API_KEY": "sk-test", "LLM_MODEL": "gpt-4o",
            "LLM_TIMEOUT_SECONDS": "42", "LLM_MAX_RETRIES": "7",
        }):
            import importlib
            import genai.config as cfg
            importlib.reload(cfg)
            import genai.llm.openai_provider as op
            importlib.reload(op)
            op.OpenAIProvider()
    kwargs = fake_openai.OpenAI.call_args[1]
    assert kwargs["timeout"] == 42
    assert kwargs["max_retries"] == 7


def test_complete_raises_on_empty_choices(openai_prov):
    # a response with no choices must raise the uniform error, not an opaque IndexError
    provider, client = openai_prov
    empty = MagicMock()
    empty.choices = []
    client.chat.completions.create.return_value = empty
    with pytest.raises(LLMProviderError):
        provider.complete("anything")


def test_complete_wraps_sdk_errors(openai_prov):
    # an SDK-level failure is re-raised as the uniform LLMProviderError
    provider, client = openai_prov
    client.chat.completions.create.side_effect = RuntimeError("rate limited")
    with pytest.raises(LLMProviderError):
        provider.complete("anything")
