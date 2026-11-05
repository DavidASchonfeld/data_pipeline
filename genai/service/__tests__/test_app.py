"""Offline unit tests for the genai-api HTTP service (genai/service/app.py).

These never call a real LLM or open any connection: genai.agent.answer is patched with a fake that
returns a canned AgentResult, and the Flask test client drives the routes in-process. They prove the
service's own contract — serialization, the input guards, and the AgentError → 502 path — independent
of the agent's behaviour (which is covered by genai/agent/__tests__)."""

from unittest.mock import patch

import pytest

from genai.agent.errors import AgentError
from genai.agent.types import AgentResult, TraceStep
from genai.retrieval.types import Passage
from genai.service.app import app

pytestmark = pytest.mark.offline


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def _canned_result() -> AgentResult:
    # A small but representative result: one retrieval step, one final-answer step, one source passage.
    return AgentResult(
        answer_text="Apple lists supply-chain concentration and FX exposure as key risks.",
        trace=[
            TraceStep(turn=1, tool="retrieve_filing_context", tool_input={"question": "risks", "ticker": "AAPL"},
                      result_summary="5 passage(s)", input_tokens=120, output_tokens=40),
            TraceStep(turn=2, tool="final_answer", result_summary="Apple lists supply-chain..."),
        ],
        sources=[Passage(id=7, text="Our business depends on...", source="sec_edgar", score=0.91,
                         metadata={"ticker": "AAPL", "section": "item1a"})],
        cost_estimate=0.0008,
    )


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_answer_serializes_full_result(client):
    with patch("genai.agent.answer", return_value=_canned_result()) as fake:
        resp = client.post("/agent/answer", json={"question": "What were Apple's biggest risks?"})

    assert resp.status_code == 200
    fake.assert_called_once_with("What were Apple's biggest risks?")
    body = resp.get_json()
    # The whole dataclass tree round-trips to JSON: answer, cost, the trace list, and the source passages.
    assert body["answer_text"].startswith("Apple lists")
    assert body["cost_estimate"] == pytest.approx(0.0008)
    assert [s["tool"] for s in body["trace"]] == ["retrieve_filing_context", "final_answer"]
    assert body["sources"][0]["metadata"]["ticker"] == "AAPL"
    # The service stamps wall-clock elapsed time onto every successful answer.
    assert "elapsed_seconds" in body


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}, {"question": 123}])
def test_blank_or_nonstring_question_is_400(client, payload):
    # Bad input must never reach the agent — it is rejected before any LLM call.
    with patch("genai.agent.answer") as fake:
        resp = client.post("/agent/answer", json=payload)
    assert resp.status_code == 400
    fake.assert_not_called()


def test_overlong_question_is_400(client):
    from genai.config import GENAI_MAX_QUESTION_CHARS
    with patch("genai.agent.answer") as fake:
        resp = client.post("/agent/answer", json={"question": "x" * (GENAI_MAX_QUESTION_CHARS + 1)})
    assert resp.status_code == 400
    fake.assert_not_called()


def test_agent_error_becomes_502(client):
    # An AgentError (the agent layer's one exception type) surfaces as a clean 502, not a stack trace.
    with patch("genai.agent.answer", side_effect=AgentError("LLM call failed on turn 1")):
        resp = client.post("/agent/answer", json={"question": "hello"})
    assert resp.status_code == 502
    assert "could not answer" in resp.get_json()["error"]
