"""Offline unit tests for the weather-summary runner — fake LLM provider + fake Snowflake, no network."""
import json
from datetime import date

import pytest

import genai.runners.summarize_runner as sr

pytestmark = pytest.mark.offline

_WEEK = "2026-05-25"  # a Monday


# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeProvider:
    # Returns canned chat() responses in order; records each call's kwargs for assertions.
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = []

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.autocommit_calls = []

    def cursor(self):
        return self.cur

    def autocommit(self, mode):
        self.autocommit_calls.append(mode)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _resp(tool_input, model="claude-sonnet-4-6-20250101"):
    # one normalised chat() response carrying a single forced tool call
    return {"tool_calls": [{"name": "t", "input": tool_input, "id": "c1"}], "model": model, "content": "", "stop_reason": "tool_use", "usage": {}}


_VALID = {"reasoning": "warm midweek", "summary": "A mild week overall, warmest on Tuesday near 78F."}
_INVALID = {"reasoning": "no summary field"}  # summary is required

# Two cities, two days each — what fetch_week_by_city's SELECT would return (city, obs_day, min, max, avg).
_TWO_CITY_ROWS = [
    ("Austin", date(2026, 5, 25), 60.0, 80.0, 70.0),
    ("Austin", date(2026, 5, 26), 62.0, 82.0, 72.0),
    ("New York", date(2026, 5, 25), 50.0, 70.0, 60.0),
    ("New York", date(2026, 5, 26), 52.0, 72.0, 62.0),
]


@pytest.fixture
def patched(monkeypatch):
    # patch the two external touchpoints; return a factory so each test sets its own rows + responses
    def _install(rows, responses):
        conn = FakeConn(rows)
        monkeypatch.setattr(sr, "get_snowflake_conn", lambda: conn)
        provider = FakeProvider(responses)
        monkeypatch.setattr("genai.llm.get_llm_provider", lambda: provider)
        return conn, provider

    return _install


# ── Tests ────────────────────────────────────────────────────────────────────


def test_happy_path_writes_one_row_per_city(patched):
    conn, _ = patched(_TWO_CITY_ROWS, [_resp(_VALID), _resp(_VALID)])
    summary = sr.run_pipeline(_WEEK)
    assert summary["mode"] == "weather"
    assert summary["week_start"] == _WEEK
    assert summary["cities"] == 2
    assert summary["rows_written"] == 2
    assert summary["errors"] == []
    # the summary must be JSON-serializable (it is printed as the last stdout line)
    assert json.loads(json.dumps(summary))["rows_written"] == 2
    assert conn.committed and conn.closed


def test_write_uses_scoped_delete_per_city(patched):
    conn, _ = patched(_TWO_CITY_ROWS, [_resp(_VALID), _resp(_VALID)])
    sr.run_pipeline(_WEEK)

    # a scoped DELETE keyed on (city, week_start) for each city — never a blanket wipe
    deletes = [c for c in conn.cur.execute_calls if "DELETE" in c[0]]
    assert len(deletes) == 2
    assert {d[1] for d in deletes} == {("Austin", _WEEK), ("New York", _WEEK)}

    # one INSERT per city, carrying the city, week, summary text, and resolved model id
    inserts = [c for c in conn.cur.execute_calls if "INSERT" in c[0]]
    assert len(inserts) == 2
    _, first = inserts[0]
    assert first[0] == "Austin" and first[1] == _WEEK
    assert first[2] == _VALID["summary"]                 # summary_text
    assert first[3] == "claude-sonnet-4-6-20250101"      # resolved model id

    # the delete + inserts run as one atomic transaction (autocommit off then back on), committed once
    assert conn.autocommit_calls == [False, True]
    assert conn.committed and not conn.rolled_back


def test_rolls_back_on_failure(patched, monkeypatch):
    conn, _ = patched(_TWO_CITY_ROWS, [_resp(_VALID), _resp(_VALID)])
    real_execute = conn.cur.execute

    def boom(sql, params=None):
        if "INSERT" in sql:
            raise RuntimeError("snowflake write failed")
        return real_execute(sql, params)

    monkeypatch.setattr(conn.cur, "execute", boom)
    with pytest.raises(RuntimeError):
        sr.run_pipeline(_WEEK)
    assert conn.rolled_back and not conn.committed
    assert conn.autocommit_calls == [False, True]


def test_retry_then_success_writes_the_row(patched):
    # Austin: invalid then valid (2 calls); New York: valid (1 call)
    conn, _ = patched(_TWO_CITY_ROWS, [_resp(_INVALID), _resp(_VALID), _resp(_VALID)])
    summary = sr.run_pipeline(_WEEK)
    assert summary["rows_written"] == 2
    assert summary["errors"] == []


def test_two_invalid_records_error_and_skips_city(patched):
    # Austin fails both attempts; New York succeeds
    conn, _ = patched(_TWO_CITY_ROWS, [_resp(_INVALID), _resp(_INVALID), _resp(_VALID)])
    summary = sr.run_pipeline(_WEEK)
    assert summary["rows_written"] == 1
    assert len(summary["errors"]) == 1
    assert "Austin" in summary["errors"][0]


def test_forces_tool_choice_at_temperature_zero(patched):
    _, provider = patched(_TWO_CITY_ROWS, [_resp(_VALID), _resp(_VALID)])
    sr.run_pipeline(_WEEK)
    assert provider.calls, "provider.chat was never called"
    first = provider.calls[0]
    assert first["temperature"] == 0                          # deterministic summaries
    assert first["tool_choice"] == "record_weather_summary"   # forced structured output


def test_no_data_writes_nothing(patched):
    # empty week → no LLM call, no write, but a valid JSON summary is still returned
    conn, provider = patched([], [])
    summary = sr.run_pipeline(_WEEK)
    assert summary["cities"] == 0 and summary["rows_written"] == 0
    assert provider.calls == []
    assert not conn.committed
    assert conn.closed


def test_default_week_start_is_a_monday():
    # the fallback when the DAG omits --week-start must land on a Monday (weekday() == 0)
    ws = sr._default_week_start()
    assert date.fromisoformat(ws).weekday() == 0
