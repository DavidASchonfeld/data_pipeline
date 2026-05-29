from __future__ import annotations

from pydantic import BaseModel, Field

from genai.extraction import prompts

# Pydantic schemas for the structured facts the LLM pulls out of a 10-K section (EPIC 4).
#
# Design rules (reference §10):
#  - The `reasoning` field comes FIRST in every model. Asking the model to think before it answers
#    (the answer fields below it) measurably improves extraction quality.
#  - Genuinely-uncertain fields are Optional so the model can leave them null instead of inventing a
#    value — a forced non-null field is an invitation to hallucinate.
#  - One schema per extract type. Each schema's JSON form (model_json_schema()) becomes the tool
#    spec the provider is forced to fill, so the model's reply is structured, not free text.


# ── Risk factors (10-K Item 1A) ────────────────────────────────────────────────


class RiskItem(BaseModel):
    # One distinct risk the company disclosed.
    title: str = Field(description="Short label for the risk, e.g. 'Supply chain concentration'")
    description: str = Field(description="One-to-two sentence summary of the risk, drawn from the filing text")
    category: str | None = Field(default=None, description="Optional grouping, e.g. 'operational', 'financial', 'regulatory'")
    severity: str | None = Field(default=None, description="Optional relative severity if the filing implies one: 'low' | 'medium' | 'high'")


class RiskFactorsExtract(BaseModel):
    # The full structured result for the risk-factors extract type.
    reasoning: str = Field(description="Brief notes on how the risks were identified from the section text")
    risks: list[RiskItem] = Field(default_factory=list, description="The distinct risk factors found in the section")


# ── Revenue / forward guidance (10-K Item 7, MD&A) ─────────────────────────────


class GuidanceItem(BaseModel):
    # One forward-looking revenue/financial guidance statement.
    statement: str = Field(description="The guidance as stated, paraphrased to one sentence")
    metric: str | None = Field(default=None, description="Optional metric the guidance concerns, e.g. 'revenue', 'operating margin'")
    direction: str | None = Field(default=None, description="Optional expected direction: 'increase' | 'decrease' | 'flat'")
    period: str | None = Field(default=None, description="Optional period the guidance covers, e.g. 'FY2024', 'next quarter'")


class RevenueGuidanceExtract(BaseModel):
    # The full structured result for the revenue-guidance extract type.
    reasoning: str = Field(description="Brief notes on how the guidance statements were identified from the section text")
    guidance_statements: list[GuidanceItem] = Field(default_factory=list, description="Forward-looking revenue/financial guidance found in the section")


# ── Extract-type registry ──────────────────────────────────────────────────────
# Single source of truth tying together: the schema, the 10-K section to read, the tool the LLM is
# forced to call, and the system prompt. The runner loops over this; the dbt accepted_values test
# uses EXTRACT_TYPE_NAMES so the warehouse and the code never drift apart.


class ExtractType(BaseModel):
    # One configured extraction job — model_config allows the arbitrary Pydantic-class field.
    model_config = {"arbitrary_types_allowed": True}

    name: str                       # stored in FCT_FILING_EXTRACTS.extract_type
    schema_model: type[BaseModel]   # the Pydantic model the LLM output must satisfy
    section_key: str                # the section name (from sections.py) to feed the LLM
    tool_name: str                  # the forced tool-call name
    prompt: str                     # the static system prompt


EXTRACT_TYPES: list[ExtractType] = [
    ExtractType(
        name="risk_factors",
        schema_model=RiskFactorsExtract,
        section_key="Item 1A - Risk Factors",
        tool_name="record_risk_factors",
        prompt=prompts.RISK_FACTORS_PROMPT,
    ),
    ExtractType(
        name="revenue_guidance",
        schema_model=RevenueGuidanceExtract,
        section_key="Item 7 - Management Discussion and Analysis",
        tool_name="record_revenue_guidance",
        prompt=prompts.REVENUE_GUIDANCE_PROMPT,
    ),
]

# The set of valid extract_type values — imported by the dbt accepted_values test so the
# allowed list in the warehouse always matches the code.
EXTRACT_TYPE_NAMES: list[str] = [et.name for et in EXTRACT_TYPES]
