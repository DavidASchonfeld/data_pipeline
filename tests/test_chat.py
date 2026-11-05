"""Offline tests for the dashboard Chat tab (dashboard/chat/).

No network and no genai imports: the agent runs in a separate pod, so the dashboard side is just an
HTTP client plus render helpers. These tests drive the render helpers directly with canned AgentResult
JSON (the shape genai/service/app.py returns) and exercise the client's error handling with requests
mocked. conftest.py already puts dashboard/ on sys.path.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from chat.callbacks import render_answer, render_meta, render_sources, render_trace
from chat.client import ChatServiceError, ask

pytestmark = pytest.mark.offline


def _canned() -> dict:
    # Mirrors the JSON genai/service/app.py emits: a filing source + a weather trace step.
    return {
        "answer_text": "Apple cites supply-chain concentration; Los Angeles was mild last week.",
        "trace": [
            {"turn": 1, "tool": "retrieve_filing_context", "tool_input": {"ticker": "AAPL"},
             "result_summary": "5 passage(s)", "error": None},
            {"turn": 2, "tool": "get_weather_summary", "tool_input": {"city": "Los Angeles"},
             "result_summary": "Mild and dry, highs near 75F.", "error": None},
            {"turn": 3, "tool": "final_answer", "result_summary": "Apple cites...", "error": None},
        ],
        "sources": [
            {"id": 7, "text": "Our business depends on a limited number of suppliers.",
             "source": "sec_edgar", "score": 0.91, "metadata": {"ticker": "AAPL", "section": "item1a"}},
        ],
        "cost_estimate": 0.0008,
        "elapsed_seconds": 4.2,
    }


def _flatten(node):
    # Collect all string text anywhere in a Dash component tree, for easy assertions.
    out = []
    children = getattr(node, "children", node)
    if isinstance(children, str):
        out.append(children)
    elif isinstance(children, (list, tuple)):
        for c in children:
            out.extend(_flatten(c))
    elif children is not None and hasattr(children, "children"):
        out.extend(_flatten(children))
    elif isinstance(children, str):
        out.append(children)
    return out


# ── render_trace ────────────────────────────────────────────────────────────


def test_render_trace_one_item_per_step():
    ul = render_trace(_canned()["trace"])
    assert len(ul.children) == 3  # one <li> per turn


def test_render_trace_marks_errors():
    trace = [{"turn": 1, "tool": "query_snowflake_marts", "result_summary": "",
              "error": "refused: only SELECT is allowed"}]
    ul = render_trace(trace)
    text = " ".join(_flatten(ul))
    assert "refused" in text  # a graceful refusal is shown, not hidden


# ── render_sources (filing passages + weather-from-trace) ────────────────────


def test_render_sources_includes_filing_and_weather():
    result = _canned()
    cards = render_sources(result["sources"], result["trace"])
    text = " ".join(t for c in cards for t in _flatten(c))
    # The filing passage shows its ticker badge + text...
    assert "AAPL" in text
    assert "limited number of suppliers" in text
    # ...and the weather source, which lives in the trace (not in sources), is surfaced too.
    assert "Los Angeles" in text
    assert "Mild and dry" in text


def test_render_sources_empty_shows_message():
    cards = render_sources([], [])
    assert "No matching sources" in " ".join(t for c in cards for t in _flatten(c))


# ── render_answer (XSS-safe) ─────────────────────────────────────────────────


def test_render_answer_is_markdown_not_raw_html():
    comp = render_answer("**bold** <script>alert(1)</script>")
    # dcc.Markdown without dangerously_allow_html escapes HTML — never enable it for untrusted output.
    assert getattr(comp, "dangerously_allow_html", False) in (False, None)
    assert "script" in comp.children  # text is preserved as data, rendered as escaped text by Dash


# ── render_meta ──────────────────────────────────────────────────────────────


def test_render_meta_shows_cost_and_elapsed():
    meta = render_meta(_canned())
    assert "$0.0008" in meta
    assert "4.2s" in meta


# ── client.ask error handling ────────────────────────────────────────────────


def test_ask_returns_json_on_200():
    fake = MagicMock(status_code=200)
    fake.json.return_value = _canned()
    with patch("chat.client.requests.post", return_value=fake):
        out = ask("What were Apple's risks?")
    assert out["answer_text"].startswith("Apple cites")


def test_ask_raises_on_connection_error():
    with patch("chat.client.requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(ChatServiceError):
            ask("hi")


def test_ask_raises_on_non_200_with_detail():
    fake = MagicMock(status_code=400)
    fake.json.return_value = {"error": "Question is too long"}
    with patch("chat.client.requests.post", return_value=fake):
        with pytest.raises(ChatServiceError, match="too long"):
            ask("x" * 5000)
