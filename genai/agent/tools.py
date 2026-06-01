# The four tools the orchestrator hands to the LLM. Each is a plain Python function whose DOCSTRING
# becomes the description the model sees (so the prose and the behaviour can never drift apart), and
# whose parameters are mirrored in a hand-written JSON schema in TOOL_SPECS below. The orchestrator
# only ever reaches a tool through TOOL_DISPATCH, and every Snowflake read is funnelled through
# query_snowflake_marts → validate_select so the safety gate is impossible to bypass.

from __future__ import annotations

import logging

from genai.agent._snowflake import get_snowflake_conn
from genai.agent.errors import AgentError
from genai.agent.sql_safety import validate_select
from genai.retrieval import Passage, retrieve
from genai.retrieval.ingest import NAMESPACE_FILINGS, NAMESPACE_WEATHER

logger = logging.getLogger(__name__)

# Snowflake table names the canned tools read from — fully qualified so they pass the safety layer's
# schema-qualification check. Local constants (not imported from airflow/) to keep genai/ standalone.
_TBL_ANOMALIES = "PIPELINE_DB.ANALYTICS.FCT_ANOMALIES"
_TBL_WEATHER_SUMMARIES = "PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES"


def query_snowflake_marts(sql_template: str, params: dict | None = None) -> list[dict]:
    """Run a read-only SELECT against the PIPELINE_DB MARTS and ANALYTICS schemas and return the rows
    as a list of dicts. Only SELECT is permitted; any other statement is refused. Always pass values
    as %(name)s placeholders in the SQL and supply them in `params` — never inline literal values.
    Available tables include MARTS.FCT_COMPANY_FINANCIALS, MARTS.FCT_WEATHER_HOURLY,
    MARTS.FCT_WEATHER_SUMMARIES, and ANALYTICS.FCT_ANOMALIES."""
    # Gate first: prove the query is a safe SELECT before opening any connection. Raises SqlSafetyError.
    safe_sql = validate_select(sql_template)

    conn = get_snowflake_conn()
    try:
        cur = conn.cursor()
        # pyformat binding — the driver substitutes values safely, so user data is never part of the SQL text.
        cur.execute(safe_sql, params or {})
        # Column names come from the cursor description; zip them with each row into a dict.
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        return rows
    except AgentError:
        raise  # a SqlSafetyError surfaced from a nested call — pass it through untouched
    except Exception as exc:
        # Any driver/network failure becomes the one agent-layer error type, with context, never swallowed.
        logger.error("Snowflake query failed: %s", exc)
        raise AgentError(f"Snowflake query failed: {exc}") from exc
    finally:
        conn.close()


def retrieve_filing_context(question: str, ticker: str | None = None) -> list[Passage]:
    """Search SEC 10-K filings for passages relevant to a question and return the best matches. Use
    this for anything about a company's risks, business, or strategy as stated in its filings.
    Optionally pass a stock ticker (e.g. 'AAPL') to restrict the search to one company."""
    # Scope BOTH search arms to the filings corpus; add the ticker filter only when one was given.
    filters: dict = {"namespace": NAMESPACE_FILINGS}
    if ticker:
        filters["ticker"] = ticker.upper()
    return retrieve(question, filters=filters)


def run_anomaly_check(ticker: str | None = None) -> list[dict]:
    """Return precomputed financial anomalies — year-over-year revenue and net-income swings the
    anomaly model flagged. Pass a ticker (e.g. 'AAPL') to scope to one company, or omit it for all.
    This reads already-computed results; it does not re-run the model."""
    # Read-only SELECT routed through query_snowflake_marts so it passes the same safety gate.
    sql = (
        "SELECT ticker, fiscal_year, revenue_yoy_pct, net_income_yoy_pct, "
        "is_anomaly, anomaly_score, detected_at "
        f"FROM {_TBL_ANOMALIES} "
    )
    params: dict = {}
    if ticker:
        sql += "WHERE ticker = %(ticker)s "
        params["ticker"] = ticker.upper()
    sql += "ORDER BY ticker, fiscal_year"
    return query_snowflake_marts(sql, params)


def get_weather_summary(city: str, week_start: str | None = None) -> str:
    """Return the plain-English weekly weather summary for a city. Only pass week_start (as
    'YYYY-MM-DD', the Monday of the week) when the user names a specific calendar week; for "recent",
    "last week", or an unspecified time, OMIT week_start to get the most recent week available."""
    if week_start:
        sql = (
            f"SELECT summary_text FROM {_TBL_WEATHER_SUMMARIES} "
            "WHERE city = %(city)s AND week_start = %(ws)s "
            "ORDER BY run_at DESC LIMIT 1"
        )
        rows = query_snowflake_marts(sql, {"city": city, "ws": week_start})
    else:
        # No week given — take the latest week we have a summary for.
        sql = (
            f"SELECT summary_text FROM {_TBL_WEATHER_SUMMARIES} "
            "WHERE city = %(city)s "
            "ORDER BY week_start DESC, run_at DESC LIMIT 1"
        )
        rows = query_snowflake_marts(sql, {"city": city})
    # A missing summary is a normal, recoverable outcome — return a clear message the model can relay.
    if not rows:
        return f"No weather summary found for {city}."
    # Snowflake returns column names upper-cased (SUMMARY_TEXT), so I don't index by a literal key — the
    # query selects exactly one column, so take that row's only value regardless of how it's cased.
    return next(iter(rows[0].values()))


# ── Tool registry ────────────────────────────────────────────────────────────────
# TOOL_SPECS is the LLM-visible catalogue: one entry per tool in the provider-neutral
# {name, description, parameters} shape that provider.chat(tools=...) expects. Descriptions are taken
# from each function's docstring so there is a single source of truth. TOOL_DISPATCH maps a tool name
# back to its function — the only place the orchestrator turns a model's choice into a real call.

TOOL_SPECS: list[dict] = [
    {
        "name": "query_snowflake_marts",
        "description": query_snowflake_marts.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "sql_template": {
                    "type": "string",
                    "description": "A single read-only SELECT with %(name)s placeholders for any values.",
                },
                "params": {
                    "type": "object",
                    "description": "Values for the placeholders, e.g. {\"ticker\": \"AAPL\"}.",
                },
            },
            "required": ["sql_template", "params"],
        },
    },
    {
        "name": "retrieve_filing_context",
        "description": retrieve_filing_context.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "What to search the filings for."},
                "ticker": {"type": "string", "description": "Optional stock ticker, e.g. 'AAPL'."},
            },
            "required": ["question"],
        },
    },
    {
        "name": "run_anomaly_check",
        "description": run_anomaly_check.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Optional stock ticker, e.g. 'AAPL'."},
            },
            "required": [],
        },
    },
    {
        "name": "get_weather_summary",
        "description": get_weather_summary.__doc__,
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Cupertino'."},
                "week_start": {
                    "type": "string",
                    "description": "Optional Monday of the week as 'YYYY-MM-DD'; omit for the latest week.",
                },
            },
            "required": ["city"],
        },
    },
]

TOOL_DISPATCH = {
    "query_snowflake_marts": query_snowflake_marts,
    "retrieve_filing_context": retrieve_filing_context,
    "run_anomaly_check": run_anomaly_check,
    "get_weather_summary": get_weather_summary,
}
