"""Offline unit tests for the extraction runner — fake LLM provider + fake Snowflake, no network."""
import json

import pytest

import genai.runners.extract_runner as er

pytestmark = pytest.mark.offline


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
    def __init__(self):
        self.execute_calls = []
        self.executemany_calls = []

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def executemany(self, sql, params):
        self.executemany_calls.append((sql, params))


class FakeConn:
    def __init__(self):
        self.cur = FakeCursor()
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
    return {"tool_calls": [{"name": "t", "input": tool_input, "id": "call_1"}], "model": model, "content": "", "stop_reason": "tool_use", "usage": {}}


_VALID_RISK = {"reasoning": "r", "risks": [{"title": "Supply chain", "description": "Few suppliers."}]}
_VALID_GUIDANCE = {"reasoning": "r", "guidance_statements": [{"statement": "Revenue to grow."}]}
_INVALID_RISK = {"reasoning": "r", "risks": [{"title": "missing description"}]}  # description is required

_FAKE_SECTIONS = {
    "filing_date": "2023-11-01",
    "report_date": "2023-09-30",
    "sections": {
        "Item 1A - Risk Factors": "Risk text.",
        "Item 7 - Management Discussion and Analysis": "MD&A text.",
    },
}


@pytest.fixture
def patched(monkeypatch):
    # patch the three external touchpoints; return the FakeConn so tests can inspect the writes
    conn = FakeConn()
    monkeypatch.setattr(er, "get_snowflake_conn", lambda: conn)
    monkeypatch.setattr("genai.extraction.edgar_fulltext.fetch_10k", lambda t, y: dict(_FAKE_SECTIONS))

    def _install_provider(responses):
        provider = FakeProvider(responses)
        monkeypatch.setattr("genai.llm.get_llm_provider", lambda: provider)
        return provider

    return conn, _install_provider


# ── Tests ────────────────────────────────────────────────────────────────────


def test_happy_path_writes_one_row_per_extract_type(patched):
    conn, install = patched
    install([_resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    summary = er.run_pipeline("AAPL", 2023)
    assert summary["ticker"] == "AAPL"
    assert summary["filing_date"] == "2023-11-01"
    assert summary["rows_written"] == 2
    assert summary["errors"] == []
    # the summary must be JSON-serializable (it is printed as the last stdout line)
    assert json.loads(json.dumps(summary))["rows_written"] == 2
    assert conn.committed and conn.closed


def test_write_uses_scoped_delete_and_parse_json_insert(patched):
    conn, install = patched
    install([_resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    er.run_pipeline("AAPL", 2023)

    # a scoped DELETE keyed on (ticker, filing_date) — never a blanket wipe (extracts table)
    delete = [c for c in conn.cur.execute_calls if "DELETE" in c[0] and "FCT_FILING_EXTRACTS" in c[0]]
    assert delete and delete[0][1] == ("AAPL", "2023-11-01")

    # extract inserts go through individual execute() calls (NOT executemany — the SELECT form can't be batch-rewritten)
    assert not conn.cur.executemany_calls, "must not use executemany for the INSERT...SELECT form"
    inserts = [c for c in conn.cur.execute_calls if "INSERT" in c[0] and "FCT_FILING_EXTRACTS" in c[0]]
    assert len(inserts) == 2, "one INSERT per extract type"
    insert_sql, first = inserts[0]
    assert "PARSE_JSON(%s)" in insert_sql and "SELECT" in insert_sql
    assert first[0] == "AAPL" and first[1] == "2023-11-01"
    assert first[3] == "risk_factors"             # extract_type
    assert json.loads(first[4])["risks"][0]["title"] == "Supply chain"  # payload is valid JSON
    assert first[5] == "claude-sonnet-4-6-20250101"  # resolved model id

    # two scoped transactions run on this conn — the section-text write, then the extracts write —
    # each toggling autocommit off then back on, both committed.
    assert conn.autocommit_calls == [False, True, False, True]
    assert conn.committed and not conn.rolled_back


def test_persists_full_section_text(patched):
    conn, install = patched
    install([_resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    summary = er.run_pipeline("AAPL", 2023)

    # the cleaned full section text is saved (untruncated) to FCT_FILING_SECTIONS — one row per section
    section_inserts = [c for c in conn.cur.execute_calls if "INSERT" in c[0] and "FCT_FILING_SECTIONS" in c[0]]
    assert len(section_inserts) == 2
    assert summary["sections_written"] == 2
    _, params = section_inserts[0]
    assert params == ("AAPL", "2023-11-01", "Item 1A - Risk Factors", "Risk text.")
    # a scoped DELETE keyed on (ticker, filing_date) precedes the section inserts
    section_delete = [c for c in conn.cur.execute_calls if "DELETE" in c[0] and "FCT_FILING_SECTIONS" in c[0]]
    assert section_delete and section_delete[0][1] == ("AAPL", "2023-11-01")


def test_write_rolls_back_on_failure(patched, monkeypatch):
    conn, install = patched
    install([_resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    # make the INSERT blow up mid-write
    real_execute = conn.cur.execute

    def boom(sql, params=None):
        if "INSERT" in sql:
            raise RuntimeError("snowflake write failed")
        return real_execute(sql, params)

    monkeypatch.setattr(conn.cur, "execute", boom)
    with pytest.raises(RuntimeError):
        er.run_pipeline("AAPL", 2023)
    # the transaction is rolled back (ticker's prior rows preserved), never committed, autocommit restored
    assert conn.rolled_back and not conn.committed
    assert conn.autocommit_calls == [False, True]


def test_retry_then_success_writes_the_row(patched):
    conn, install = patched
    # risk_factors: invalid then valid (2 calls); revenue_guidance: valid (1 call)
    install([_resp(_INVALID_RISK), _resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    summary = er.run_pipeline("AAPL", 2023)
    assert summary["rows_written"] == 2
    assert summary["errors"] == []


def test_two_invalid_records_error_and_skips_row(patched):
    conn, install = patched
    # risk_factors fails both attempts; revenue_guidance succeeds
    install([_resp(_INVALID_RISK), _resp(_INVALID_RISK), _resp(_VALID_GUIDANCE)])
    summary = er.run_pipeline("AAPL", 2023)
    assert summary["rows_written"] == 1
    assert len(summary["errors"]) == 1
    assert "risk_factors" in summary["errors"][0]


def test_forces_tool_choice_at_temperature_zero(patched):
    conn, install = patched
    provider = install([_resp(_VALID_RISK), _resp(_VALID_GUIDANCE)])
    er.run_pipeline("AAPL", 2023)

    assert provider.calls, "provider.chat was never called"
    first = provider.calls[0]
    assert first["temperature"] == 0                       # deterministic extraction
    assert first["tool_choice"] == "record_risk_factors"   # forced structured output
