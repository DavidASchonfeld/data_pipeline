"""Offline unit tests for provider.build_tool_turn — the provider-specific step that threads tool
results back into a conversation. Verifies the Anthropic-shaped default and the OpenAI override."""
import json

import pytest

from genai.llm.base import LLMProvider
from genai.llm.openai_provider import OpenAIProvider

pytestmark = pytest.mark.offline


class _DummyProvider(LLMProvider):
    # Minimal concrete provider so we can exercise the base (Anthropic-shaped) build_tool_turn default.
    def complete(self, prompt, max_tokens=1024):
        return ""

    def chat(self, *a, **k):
        return {}


_RESPONSE = {
    "content": "let me check",
    "tool_calls": [{"name": "get_weather_summary", "input": {"city": "Cupertino"}, "id": "call_1"}],
}
_OUTPUTS = {"call_1": "Cupertino was mild."}


def test_anthropic_default_shape():
    msgs = _DummyProvider().build_tool_turn(_RESPONSE, _OUTPUTS)
    assert len(msgs) == 2
    assistant, user = msgs
    # assistant turn: the optional text block, then a tool_use block carrying the call id
    assert assistant["role"] == "assistant"
    assert {"type": "text", "text": "let me check"} in assistant["content"]
    tool_use = [b for b in assistant["content"] if b["type"] == "tool_use"][0]
    assert tool_use["id"] == "call_1" and tool_use["name"] == "get_weather_summary"
    # user turn: a tool_result keyed by the same id, carrying the tool's output
    assert user["role"] == "user"
    assert user["content"][0] == {
        "type": "tool_result", "tool_use_id": "call_1", "content": "Cupertino was mild.",
    }


def test_openai_override_shape():
    # build via object.__new__ to skip __init__ (which would import the SDK / require an API key)
    provider = object.__new__(OpenAIProvider)
    msgs = provider.build_tool_turn(_RESPONSE, _OUTPUTS)
    assistant = msgs[0]
    assert assistant["role"] == "assistant"
    tc = assistant["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["type"] == "function"
    # OpenAI carries tool arguments as a JSON STRING, not a dict
    assert json.loads(tc["function"]["arguments"]) == {"city": "Cupertino"}
    # one role:"tool" message per call, keyed by tool_call_id
    tool_msg = msgs[1]
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": "Cupertino was mild."}
