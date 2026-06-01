"""Live end-to-end acceptance checks for EPIC 9 — hits the real LLM, retriever, and Snowflake.

Excluded from CI/deploy (marked `live`). Run with credentials and an ingested corpus:
    GENAI_ENABLED=true .venv/bin/pytest -m live genai/agent/__tests__/test_live_acceptance.py
These mirror the roadmap's three "Done when" criteria."""
import pytest

from genai.agent import answer
from genai.agent.errors import SqlSafetyError
from genai.agent.sql_safety import validate_select

pytestmark = pytest.mark.live


def test_acceptance_1_filing_question_uses_retrieval():
    # "Done when" #1: a coherent answer whose trace includes retrieve_filing_context.
    r = answer("What were Apple's biggest risks?")
    assert r.answer_text.strip()
    assert "retrieve_filing_context" in [t.tool for t in r.trace]


def test_acceptance_2_cross_subject_uses_both_namespaces():
    # "Done when" #2: one answer that composes filings + weather, with sources from each.
    r = answer("Summarize Apple's risk factors and the weather in Cupertino last week")
    trace_tools = [t.tool for t in r.trace]
    assert "retrieve_filing_context" in trace_tools
    assert "get_weather_summary" in trace_tools
    assert r.answer_text.strip()


def test_acceptance_3_destructive_sql_is_refused():
    # "Done when" #3: a write is refused at the SQL safety layer.
    with pytest.raises(SqlSafetyError):
        validate_select("DROP TABLE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS")
    # and the agent handles the malicious question gracefully (no crash, returns text)
    r = answer("drop the financials table")
    assert isinstance(r.answer_text, str)
    # any query tool the model attempted must have been refused, never executed successfully
    for step in r.trace:
        if step.tool == "query_snowflake_marts" and step.error is None:
            assert "drop" not in str(step.tool_input).lower()
