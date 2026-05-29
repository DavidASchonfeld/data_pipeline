"""Offline unit tests for the static extraction prompts — no network, no API key."""
from genai.extraction import prompts, schemas


def test_each_extract_type_has_a_nonempty_prompt():
    # every registered extract type must carry a usable, non-empty system prompt
    for et in schemas.EXTRACT_TYPES:
        assert isinstance(et.prompt, str)
        assert len(et.prompt.strip()) > 0


def test_prompts_carry_the_grounding_rules():
    # the anti-hallucination / anti-injection rules must be present in every prompt
    for prompt in (prompts.RISK_FACTORS_PROMPT, prompts.REVENUE_GUIDANCE_PROMPT):
        lower = prompt.lower()
        assert "only" in lower            # answer only from the filing text
        assert "untrusted" in lower       # treat the filing text as data, not instructions
        assert "null" in lower            # leave uncertain fields null


def test_prompts_are_static_strings():
    # module-level constants are plain str (no runtime templating) — keeps caching + determinism
    assert type(prompts.RISK_FACTORS_PROMPT) is str
    assert type(prompts.REVENUE_GUIDANCE_PROMPT) is str
