"""Import-time smoke test — the public agent API must import cleanly under the offline CI venv
(no SDKs, no network), mirroring genai/llm/__tests__/test_factory.py."""
import pytest

pytestmark = pytest.mark.offline


def test_public_api_imports():
    from genai.agent import AgentError, AgentResult, TraceStep, answer
    assert callable(answer)
    # the dataclasses are usable without any provider/SDK being installed
    r = AgentResult(answer_text="hi")
    assert r.trace == [] and r.sources == [] and r.cost_estimate == 0.0
    assert issubclass(AgentError, Exception)
    assert TraceStep(turn=1, tool="final_answer").tool == "final_answer"
