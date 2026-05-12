"""Offline unit tests for AnthropicProvider — mock the HTTP client so no API calls are made."""
from unittest.mock import MagicMock


def _fake_response(text="Hello", stop_reason="end_turn", tool_name=None, tool_input=None, tool_id=None, input_tokens=10, output_tokens=5):
    # build a minimal fake Anthropic response that matches the shape _normalise_response expects
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    content = [text_block]
    if tool_name:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = tool_name
        tool_block.input = tool_input or {}
        tool_block.id = tool_id or "call_abc"
        content.append(tool_block)

    response = MagicMock()
    response.content = content
    response.stop_reason = stop_reason
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


def test_complete_returns_text(anthropic_prov):
    # confirm complete() extracts the text from content[0].text
    provider, client = anthropic_prov
    client.messages.create.return_value = _fake_response(text="Hello")
    result = provider.complete("Say hello.", max_tokens=20)
    assert result == "Hello"
    client.messages.create.assert_called_once()


def test_complete_passes_prompt_as_user_message(anthropic_prov):
    # confirm the prompt is sent as a single user message
    provider, client = anthropic_prov
    client.messages.create.return_value = _fake_response()
    provider.complete("Test prompt", max_tokens=10)
    call_kwargs = client.messages.create.call_args[1]
    assert call_kwargs["messages"] == [{"role": "user", "content": "Test prompt"}]


def test_chat_returns_normalised_shape(anthropic_prov):
    # confirm chat() returns a dict with all four required keys
    provider, client = anthropic_prov
    client.messages.create.return_value = _fake_response(text="4")
    result = provider.chat(messages=[{"role": "user", "content": "2+2?"}], max_tokens=20)
    assert "content" in result
    assert "stop_reason" in result
    assert "usage" in result
    assert "tool_calls" in result
    assert result["content"] == "4"
    assert result["stop_reason"] == "end_turn"
    assert result["usage"]["input_tokens"] == 10
    assert result["usage"]["output_tokens"] == 5
    assert result["tool_calls"] == []


def test_chat_sends_system_as_kwarg(anthropic_prov):
    # confirm system becomes a top-level kwarg (a list of blocks), not prepended to messages
    provider, client = anthropic_prov
    client.messages.create.return_value = _fake_response()
    provider.chat(messages=[{"role": "user", "content": "Hi"}], system="You are helpful.", max_tokens=20)
    call_kwargs = client.messages.create.call_args[1]
    assert "system" in call_kwargs
    assert call_kwargs["system"][0]["text"] == "You are helpful."
    # messages list must NOT contain a system-role entry — Anthropic uses a separate kwarg
    for msg in call_kwargs["messages"]:
        assert msg.get("role") != "system"


def test_chat_translates_tools_to_anthropic_format(anthropic_prov):
    # confirm the generic tool spec is converted to Anthropic's format (input_schema, not parameters)
    provider, client = anthropic_prov
    client.messages.create.return_value = _fake_response()
    tools = [{"name": "get_weather", "description": "Get weather", "parameters": {"type": "object", "properties": {}}}]
    provider.chat(messages=[{"role": "user", "content": "Weather?"}], tools=tools, max_tokens=20)
    sent_tools = client.messages.create.call_args[1]["tools"]
    assert sent_tools[0]["name"] == "get_weather"
    assert "input_schema" in sent_tools[0]
    assert "parameters" not in sent_tools[0]  # Anthropic uses input_schema, not parameters


def test_chat_parses_tool_calls_in_response(anthropic_prov):
    # confirm tool_use blocks in the response are extracted into the standard tool_calls shape
    provider, client = anthropic_prov
    response = _fake_response(text="", tool_name="get_weather", tool_input={"city": "NYC"}, tool_id="call_abc")
    client.messages.create.return_value = response
    result = provider.chat(messages=[{"role": "user", "content": "Weather?"}], max_tokens=20)
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "get_weather"
    assert result["tool_calls"][0]["input"] == {"city": "NYC"}
