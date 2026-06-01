# Token-and-dollar bookkeeping for the per-query cost cap. There is no tokenizer dependency here on
# purpose: the orchestrator only needs a rough projection to decide whether the NEXT turn would blow
# the budget, and the ACTUAL cost is always taken from the provider's reported usage afterwards. The
# 4-chars-per-token estimate matches the constant the Anthropic provider already uses (anthropic_provider.py).

from __future__ import annotations

from genai.config import LLM_PRICING, LLM_PRICING_DEFAULT

# Rough bytes-per-token ratio for English text — used only to PROJECT an upcoming call's input size.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate how many tokens a string is, without loading a tokenizer. Always at least 1 so an
    empty message still counts as a token's worth of overhead."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _match_pricing(model: str) -> dict[str, float]:
    # Find the price entry whose key is the longest prefix of the model id — so "claude-sonnet-4-5"
    # matches a dated id like "claude-sonnet-4-5-20250101". Fall back to the conservative default.
    best_key = ""
    for key in LLM_PRICING:
        if model.startswith(key) and len(key) > len(best_key):
            best_key = key
    return LLM_PRICING[best_key] if best_key else LLM_PRICING_DEFAULT


def cost_of(usage: dict, model: str) -> float:
    """Convert a provider usage dict ({input_tokens, output_tokens}) into a USD cost using the
    per-model price list. Missing token counts are treated as 0 so a malformed usage block can never
    crash the cost accounting."""
    rate = _match_pricing(model)
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return input_tokens / 1_000_000 * rate["input"] + output_tokens / 1_000_000 * rate["output"]


def project_call_cost(projected_input_tokens: int, max_output_tokens: int, model: str) -> float:
    """Worst-case cost of a call we have not made yet: bill the estimated input plus the FULL output
    budget. Projecting output at its ceiling means the cap stops a call it cannot afford BEFORE
    spending, never after."""
    return cost_of(
        {"input_tokens": projected_input_tokens, "output_tokens": max_output_tokens}, model
    )
