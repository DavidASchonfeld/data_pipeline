"""Offline unit tests for the cost helpers — token estimation and USD pricing with prefix matching."""
import pytest

from genai.agent.cost import _match_pricing, cost_of, estimate_tokens, project_call_cost
from genai.config import LLM_PRICING_DEFAULT

pytestmark = pytest.mark.offline


def test_estimate_tokens_monotonic_and_at_least_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("a" * 80) > estimate_tokens("a" * 40)


def test_match_pricing_longest_prefix_wins():
    # a dated model id must resolve to the base "claude-sonnet-4-5" entry
    rate = _match_pricing("claude-sonnet-4-5-20250101")
    assert rate == {"input": 3.00, "output": 15.00}


def test_match_pricing_unknown_model_falls_back_to_default():
    assert _match_pricing("some-unreleased-model") == LLM_PRICING_DEFAULT


def test_cost_of_computes_per_million_token_price():
    # 1M input + 1M output at the Sonnet rate = $3 + $15 = $18
    cost = cost_of({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, "claude-sonnet-4-5")
    assert cost == pytest.approx(18.00)


def test_cost_of_tolerates_missing_usage_fields():
    assert cost_of({}, "claude-sonnet-4-5") == 0.0


def test_project_call_cost_bills_full_output_budget():
    # projection prices the output at its ceiling so the cap stops a call BEFORE overspending
    projected = project_call_cost(0, 1_000_000, "claude-sonnet-4-5")
    assert projected == pytest.approx(15.00)
