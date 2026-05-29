-- Filing-extract dimension — joins the LLM-extracted 10-K facts to the company dimension so the
-- dashboard/agent can show "Apple's risk factors" with the entity name and CIK attached.
-- The extract rows are written by genai/runners/extract_runner.py (not dbt); this model reads them
-- from the ANALYTICS source and enriches them with dim_company.
-- tag:genai_filings — built/tested via `dbt run --select tag:genai_filings` from the genai DAG.
{{
    config(
        materialized='table',
        tags=['genai_filings']
    )
}}

select
    e.ticker,
    c.entity_name,                 -- from dim_company; null for any ticker not in the financials mart
    c.cik,
    e.filing_date,
    e.section,
    e.extract_type,
    e.payload,                     -- VARIANT: the full structured extract (risks / guidance list)
    e.model_name,                  -- resolved LLM model id that produced the extract (reproducibility)
    e.run_at
from {{ source('analytics', 'FCT_FILING_EXTRACTS') }} e
left join {{ ref('dim_company') }} c              -- left join: keep extracts even if the company isn't in dim_company yet
    on e.ticker = c.ticker
