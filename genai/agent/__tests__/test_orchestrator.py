"""Offline unit tests for the orchestrator loop — fake provider + fake retriever, no network.

The fake provider subclasses the real LLMProvider so it inherits the genuine build_tool_turn(), which
means these tests also exercise the Anthropic-shaped tool-result threading the orchestrator relies on."""
import pytest

import genai.agent.orchestrator as orch
import genai.agent.tools as tools
from genai.llm.base import LLMProvider
from genai.retrieval import Passage

pytestmark = pytest.mark.offline


# ── Fake provider ────────────────────────────────────────────────────────────────


class FakeProvider(LLMProvider):
    # Plays back canned chat() responses in order; once exhausted it keeps requesting a tool (used by
    # the max-turns test). Records each call's kwargs so tests can inspect the threaded messages.
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, prompt, max_tokens=1024):
        return ""

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return _tool_resp("retrieve_filing_context", {"question": "x"})


def _tool_resp(name, tool_input, call_id="c1", model="claude-sonnet-4-5"):
    return {
        "content": "", "stop_reason": "tool_use", "model": model,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "tool_calls": [{"name": name, "input": tool_input, "id": call_id}],
    }


def _final_resp(text, model="claude-sonnet-4-5"):
    return {
        "content": text, "stop_reason": "end_turn", "model": model,
        "usage": {"input_tokens": 10, "output_tokens": 5}, "tool_calls": [],
    }


@pytest.fixture
def install(monkeypatch):
    # Wire a fake provider and a fake retriever; raise the cost cap so cost isn't the variable under test.
    def _install(responses, passages=None):
        provider = FakeProvider(responses)
        monkeypatch.setattr("genai.llm.get_llm_provider", lambda: provider)
        monkeypatch.setattr(tools, "retrieve", lambda question, filters=None: list(passages or []))
        monkeypatch.setattr(orch, "GENAI_MAX_COST_PER_QUERY", 100.0)
        return provider

    return _install


# ── Tests ────────────────────────────────────────────────────────────────────


def test_tool_call_then_final_answer(install):
    passages = [Passage(id=7, text="Apple faces supply risk.", source="sec_edgar", score=0.9,
                        metadata={"ticker": "AAPL"})]
    provider = install([_tool_resp("retrieve_filing_context", {"question": "risks", "ticker": "AAPL"}),
                        _final_resp("Apple's biggest risks include supply chain.")], passages=passages)

    result = orch.answer("What were Apple's biggest risks?")

    assert result.answer_text == "Apple's biggest risks include supply chain."
    trace_tools = [s.tool for s in result.trace]
    assert "retrieve_filing_context" in trace_tools
    assert trace_tools[-1] == "final_answer"
    # the retrieved passage is carried through as a citable source
    assert result.sources and result.sources[0].id == 7
    assert result.cost_estimate > 0


def test_tool_results_are_threaded_back_to_the_model(install):
    provider = install([_tool_resp("retrieve_filing_context", {"question": "risks"}),
                        _final_resp("done")],
                       passages=[Passage(id=1, text="ctx", source="sec_edgar", score=1.0, metadata={})])
    orch.answer("q")

    # the second chat() call must carry the assistant tool_use turn + a user tool_result turn
    second_messages = provider.calls[1]["messages"]
    assert any(m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in m["content"])
               for m in second_messages if isinstance(m["content"], list))
    assert any(m["role"] == "user" and any(b.get("type") == "tool_result" for b in m["content"])
               for m in second_messages if isinstance(m["content"], list))


def test_cross_subject_two_tools_then_answer(install, monkeypatch):
    # filings tool first, then weather tool, then a final answer — proving the single agent composes
    # across subjects (the shape of EPIC 9 acceptance check 2, offline).
    monkeypatch.setattr(tools, "get_snowflake_conn", lambda: _WeatherConn())
    provider = install([
        _tool_resp("retrieve_filing_context", {"question": "Apple risks", "ticker": "AAPL"}, call_id="a"),
        _tool_resp("get_weather_summary", {"city": "Cupertino"}, call_id="b"),
        _final_resp("Apple's risks are X; Cupertino was mild."),
    ], passages=[Passage(id=3, text="risk", source="sec_edgar", score=1.0, metadata={"ticker": "AAPL"})])

    result = orch.answer("Summarize Apple's risk factors and the weather in Cupertino last week")
    trace_tools = [s.tool for s in result.trace]
    assert "retrieve_filing_context" in trace_tools and "get_weather_summary" in trace_tools


def test_cost_cap_stops_before_calling(monkeypatch):
    provider = FakeProvider([_final_resp("never reached")])
    monkeypatch.setattr("genai.llm.get_llm_provider", lambda: provider)
    monkeypatch.setattr(orch, "GENAI_MAX_COST_PER_QUERY", 0.0)   # nothing is affordable

    result = orch.answer("q")
    assert provider.calls == []                                  # no LLM call was made
    assert result.trace[-1].tool == "cost_cap_reached"
    assert "cost limit" in result.answer_text


def test_max_turns_terminates_with_fallback(install, monkeypatch):
    monkeypatch.setattr(orch, "GENAI_AGENT_MAX_TURNS", 3)
    # provider always asks for a tool (responses exhaust immediately → fallback tool calls)
    provider = install([], passages=[])

    result = orch.answer("loop forever?")
    tool_steps = [s for s in result.trace if s.tool == "retrieve_filing_context"]
    assert len(tool_steps) == 3                                  # stopped exactly at the turn budget
    assert "all my available steps" in result.answer_text


def test_tool_error_is_recorded_and_loop_continues(install):
    # The model asks to drop a table; the safety layer refuses, the error is recorded and fed back,
    # and the loop still reaches a final answer rather than crashing.
    provider = install([
        _tool_resp("query_snowflake_marts",
                   {"sql_template": "DROP TABLE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS", "params": {}}),
        _final_resp("I can't modify data, but here's what I can tell you."),
    ])
    result = orch.answer("drop the financials table")
    refused = [s for s in result.trace if s.tool == "query_snowflake_marts"]
    assert refused and refused[0].error is not None
    assert result.answer_text.startswith("I can't modify data")


# Small fake Snowflake connection for the cross-subject test's weather tool.
class _WeatherCursor:
    description = [("summary_text",)]
    def execute(self, sql, params=None): pass
    def fetchall(self): return [("Cupertino was mild and sunny.",)]
    def close(self): pass


class _WeatherConn:
    def cursor(self): return _WeatherCursor()
    def close(self): pass


# ── Baseline-skewing bug fixes ───────────────────────────────────────────────────


def test_retriever_failure_is_fed_back_not_fatal(install, monkeypatch):
    # A pgvector/BM25 outage raises RetrieverError; the orchestrator records it and keeps going
    # instead of crashing the whole question.
    from genai.retrieval import RetrieverError

    install([_tool_resp("retrieve_filing_context", {"question": "risks"}),
             _final_resp("Search is unavailable right now.")])

    def _boom(question, filters=None):
        raise RetrieverError("pgvector down")

    monkeypatch.setattr(tools, "retrieve", _boom)
    result = orch.answer("What were Apple's biggest risks?")
    failed = [s for s in result.trace if s.tool == "retrieve_filing_context"]
    assert failed and "pgvector down" in failed[0].error
    assert result.answer_text == "Search is unavailable right now."


def test_turn_usage_is_recorded_once_when_several_tools_run(install, monkeypatch):
    # One LLM turn asks for two tools; its tokens must be counted once, not once per tool.
    monkeypatch.setattr(tools, "get_snowflake_conn", lambda: _WeatherConn())
    two_tools = {
        "content": "", "stop_reason": "tool_use", "model": "claude-sonnet-4-5",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "tool_calls": [
            {"name": "retrieve_filing_context", "input": {"question": "risks"}, "id": "a"},
            {"name": "get_weather_summary", "input": {"city": "Cupertino"}, "id": "b"},
        ],
    }
    install([two_tools, _final_resp("done")])
    result = orch.answer("risks and weather")
    turn1 = [s for s in result.trace if s.turn == 1]
    assert len(turn1) == 2
    assert sum(s.input_tokens for s in turn1) == 10
    assert sum(s.output_tokens for s in turn1) == 5


def test_unexpected_tool_arguments_are_fed_back(install):
    # The model invents an argument the tool doesn't take; that becomes a recoverable error message.
    provider = install([_tool_resp("retrieve_filing_context", {"question": "risks", "year": 2024}),
                        _final_resp("ok")])
    result = orch.answer("risks")
    step = [s for s in result.trace if s.tool == "retrieve_filing_context"][0]
    assert step.error and "bad arguments" in step.error
    assert result.answer_text == "ok"
    # The error text was threaded back to the model on the next call.
    assert "bad arguments" in str(provider.calls[1]["messages"])


def test_non_dict_tool_input_is_fed_back(install):
    # A malformed tool input (not an object) is refused cleanly rather than raising TypeError.
    install([_tool_resp("run_anomaly_check", ["AAPL"]), _final_resp("ok")])
    result = orch.answer("anomalies")
    step = [s for s in result.trace if s.tool == "run_anomaly_check"][0]
    assert step.error and "must be an object" in step.error
