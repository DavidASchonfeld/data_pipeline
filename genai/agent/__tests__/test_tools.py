"""Offline unit tests for the four agent tools — fake Snowflake + fake retriever, no network."""
import pytest

import genai.agent.tools as tools
from genai.agent.errors import SqlSafetyError
from genai.retrieval import Passage

pytestmark = pytest.mark.offline


# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, rows, columns):
        self._rows = rows
        # cursor.description is a list of column tuples; only the first element (the name) is read.
        self.description = [(c,) for c in columns]
        self.execute_calls = []

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows, columns):
        self.cur = FakeCursor(rows, columns)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.fixture
def fake_snowflake(monkeypatch):
    # Install a fake Snowflake connection returning the given rows/columns; return it for assertions.
    def _install(rows, columns):
        conn = FakeConn(rows, columns)
        monkeypatch.setattr(tools, "get_snowflake_conn", lambda: conn)
        return conn

    return _install


# ── query_snowflake_marts ────────────────────────────────────────────────────────


def test_query_returns_rows_as_dicts_and_closes(fake_snowflake):
    conn = fake_snowflake([("AAPL", 2023)], ["ticker", "fiscal_year"])
    out = tools.query_snowflake_marts(
        "SELECT ticker, fiscal_year FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS WHERE ticker = %(t)s",
        {"t": "AAPL"},
    )
    assert out == [{"ticker": "AAPL", "fiscal_year": 2023}]
    # values are passed to the driver as params, never inlined into the SQL text
    sql, params = conn.cur.execute_calls[0]
    assert params == {"t": "AAPL"}
    assert "%(t)s" in sql
    assert conn.closed


def test_query_refuses_non_select_before_connecting(fake_snowflake):
    conn = fake_snowflake([], [])
    with pytest.raises(SqlSafetyError):
        tools.query_snowflake_marts("DROP TABLE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS", {})
    # the safety gate fires before any connection/execute happens
    assert conn.cur.execute_calls == []


# ── retrieve_filing_context ──────────────────────────────────────────────────────


def test_retrieve_scopes_to_filings_namespace_and_uppercases_ticker(monkeypatch):
    seen = {}

    def fake_retrieve(question, filters=None):
        seen["question"], seen["filters"] = question, filters
        return [Passage(id=1, text="risk", source="sec_edgar", score=0.9, metadata={"ticker": "AAPL"})]

    monkeypatch.setattr(tools, "retrieve", fake_retrieve)
    out = tools.retrieve_filing_context("biggest risks", ticker="aapl")
    assert seen["filters"] == {"namespace": "sec_filings", "ticker": "AAPL"}
    assert out[0].text == "risk"


def test_retrieve_without_ticker_omits_ticker_filter(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools, "retrieve", lambda question, filters=None: seen.update(filters=filters) or [])
    tools.retrieve_filing_context("anything")
    assert seen["filters"] == {"namespace": "sec_filings"}


# ── run_anomaly_check ──────────────────────────────────────────────────────────


def test_anomaly_check_builds_parameterized_query(fake_snowflake):
    conn = fake_snowflake([("AAPL", 2023, 0.1, 0.2, True, -0.5, None)],
                          ["ticker", "fiscal_year", "revenue_yoy_pct", "net_income_yoy_pct",
                           "is_anomaly", "anomaly_score", "detected_at"])
    out = tools.run_anomaly_check("aapl")
    assert out[0]["ticker"] == "AAPL" and out[0]["is_anomaly"] is True
    sql, params = conn.cur.execute_calls[0]
    assert "FCT_ANOMALIES" in sql and params == {"ticker": "AAPL"}


def test_anomaly_check_without_ticker_has_no_where(fake_snowflake):
    conn = fake_snowflake([], ["ticker", "fiscal_year", "revenue_yoy_pct", "net_income_yoy_pct",
                               "is_anomaly", "anomaly_score", "detected_at"])
    tools.run_anomaly_check()
    sql, params = conn.cur.execute_calls[0]
    assert "WHERE" not in sql and params == {}


# ── get_weather_summary ──────────────────────────────────────────────────────────


def test_weather_summary_returns_text(fake_snowflake):
    # Snowflake returns column names UPPER-cased — get_weather_summary must not index a lowercase key.
    fake_snowflake([("A mild, sunny week.",)], ["SUMMARY_TEXT"])
    assert tools.get_weather_summary("Los Angeles") == "A mild, sunny week."


def test_weather_summary_missing_returns_friendly_message(fake_snowflake):
    fake_snowflake([], ["SUMMARY_TEXT"])
    out = tools.get_weather_summary("Atlantis", week_start="2026-05-25")
    assert "No weather summary found" in out and "Atlantis" in out
