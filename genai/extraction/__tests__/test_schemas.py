"""Offline unit tests for the extraction Pydantic schemas + registry — no network, no API key."""
import pytest

from genai.extraction import schemas


def test_risk_factors_validates_good_payload():
    # a well-formed payload (reasoning + risks) validates and keeps its fields
    model = schemas.RiskFactorsExtract.model_validate(
        {
            "reasoning": "Found three risks in the section.",
            "risks": [
                {"title": "Supply chain", "description": "Reliance on a few suppliers.", "category": "operational", "severity": "high"},
                {"title": "FX", "description": "Exposure to currency swings."},
            ],
        }
    )
    assert len(model.risks) == 2
    # optional fields left out default to None, never a guessed value
    assert model.risks[1].category is None
    assert model.risks[1].severity is None


def test_revenue_guidance_validates_good_payload():
    model = schemas.RevenueGuidanceExtract.model_validate(
        {
            "reasoning": "One guidance statement found.",
            "guidance_statements": [
                {"statement": "Revenue to grow mid-single digits.", "metric": "revenue", "direction": "increase", "period": "FY2024"}
            ],
        }
    )
    assert model.guidance_statements[0].metric == "revenue"


def test_bad_payload_is_rejected():
    # a risk item missing its required description must fail validation, not silently pass
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.RiskFactorsExtract.model_validate(
            {"reasoning": "x", "risks": [{"title": "only a title"}]}
        )


@pytest.mark.parametrize("model_cls", [schemas.RiskFactorsExtract, schemas.RevenueGuidanceExtract])
def test_json_schema_shape(model_cls):
    # model_json_schema() must be a usable tool spec: an object schema with properties
    spec = model_cls.model_json_schema()
    assert spec["type"] == "object"
    assert "properties" in spec and spec["properties"]


@pytest.mark.parametrize("model_cls", [schemas.RiskFactorsExtract, schemas.RevenueGuidanceExtract])
def test_reasoning_field_comes_first(model_cls):
    # reasoning must be the first field so the model thinks before it answers (reference §10)
    assert list(model_cls.model_fields.keys())[0] == "reasoning"


def test_registry_is_consistent():
    # every registered extract type names a real schema, section, tool and prompt
    assert schemas.EXTRACT_TYPES, "registry must not be empty"
    for et in schemas.EXTRACT_TYPES:
        assert et.name and et.tool_name and et.section_key
        assert et.prompt.strip()
        assert issubclass(et.schema_model, schemas.BaseModel)
    # the names list (used by the dbt accepted_values test) matches the registry exactly
    assert schemas.EXTRACT_TYPE_NAMES == [et.name for et in schemas.EXTRACT_TYPES]
    # names are unique — extract_type is a key dimension in the warehouse
    assert len(schemas.EXTRACT_TYPE_NAMES) == len(set(schemas.EXTRACT_TYPE_NAMES))
