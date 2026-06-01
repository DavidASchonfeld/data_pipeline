"""Offline unit tests for the weather-summary prompt + schema (EPIC 5)."""
import pytest
from pydantic import ValidationError

from genai.extraction.weather_summary import (
    WEATHER_SUMMARY_PROMPT,
    WEATHER_SUMMARY_TOOL_NAME,
    WeatherSummary,
)

pytestmark = pytest.mark.offline


def test_schema_validates_a_complete_summary():
    m = WeatherSummary.model_validate({"reasoning": "warm midweek", "summary": "A mild week, warmest Tuesday."})
    assert m.summary.startswith("A mild week")


def test_schema_requires_the_summary_field():
    # summary is required — a forced-but-empty field would invite a hallucinated answer, so it must be present
    with pytest.raises(ValidationError):
        WeatherSummary.model_validate({"reasoning": "no summary"})


def test_reasoning_field_comes_before_summary():
    # reasoning-first convention (think before answering) — same ordering rule as the extraction schemas
    fields = list(WeatherSummary.model_fields)
    assert fields == ["reasoning", "summary"]


def test_prompt_is_a_static_string_with_grounding_and_anti_injection_rules():
    # plain str (cacheable, deterministic) that bakes in the ground-only + ignore-embedded-commands rules
    assert isinstance(WEATHER_SUMMARY_PROMPT, str) and len(WEATHER_SUMMARY_PROMPT) > 200
    assert "ONLY" in WEATHER_SUMMARY_PROMPT
    assert "untrusted" in WEATHER_SUMMARY_PROMPT


def test_tool_name_is_stable():
    # the runner forces tool_choice on this exact name — keep them in lockstep
    assert WEATHER_SUMMARY_TOOL_NAME == "record_weather_summary"
