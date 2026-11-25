# Tests for the Bronze command-line runner: the last stdout line is always one JSON summary (the DAG parses it),
# exit codes match success/failure, dry-run writes nothing, and config problems are reported, not crashed on.

from __future__ import annotations

import json

import pytest

from agent_data.domains.edgar import run_bronze
from agent_data.domains.edgar.__tests__.fakes import FakeSession, make_client
from agent_data.engine.lake.delta_store import DeltaLakeStore

pytestmark = pytest.mark.offline


def _last_json(capsys) -> dict:
    # Parse the final stdout line, exactly as the DAG does.
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.fixture
def lake(tmp_path) -> DeltaLakeStore:
    return DeltaLakeStore(str(tmp_path / "lake"))


def test_ingest_then_rerun_via_the_cli(lake, capsys):
    args = ["--tickers", "AAPL", "JPM"]
    assert run_bronze.main(args, lake=lake, client=make_client()) == 0
    first = _last_json(capsys)
    assert (first["mode"], first["landed"], first["skipped"], first["verify"]["ok"]) == ("ingest", 5, 1, True)
    session = FakeSession()
    assert run_bronze.main(args, lake=lake, client=make_client(session)) == 0
    second = _last_json(capsys)
    assert (second["discovered"], session.downloads()) == (0, [])


def test_dry_run_lists_candidates_and_writes_nothing(lake, capsys, tmp_path):
    session = FakeSession()
    assert run_bronze.main(["--tickers", "AAPL", "--years", "2024", "--dry-run"], lake=lake,
                           client=make_client(session)) == 0
    out = _last_json(capsys)
    assert out["would_fetch"] == ["AAPL FY2024 10-K 0000320193-24-000123"]
    assert session.downloads() == [] and not (tmp_path / "lake").exists()


def test_verify_only_needs_no_sec_client(lake, capsys):
    assert run_bronze.main(["--verify-only", "--deep"], lake=lake) == 0
    out = _last_json(capsys)
    assert out["mode"] == "verify" and out["verify"]["deep"] is True


def test_partial_failure_exits_1_with_details(lake, capsys):
    session = FakeSession()
    session.routes = {u: r for u, r in session.routes.items() if not u.endswith("aapl-20240928.htm")}
    assert run_bronze.main(["--tickers", "AAPL"], lake=lake, client=make_client(session)) == 1
    out = _last_json(capsys)
    assert out["ok"] is False and out["failures"][0]["accession"] == "0000320193-24-000123"


def test_config_error_is_reported_as_json(clean_env, capsys, monkeypatch):
    # aws profile with no LAKE_URI: the runner must explain, not crash with a traceback.
    monkeypatch.setattr(run_bronze, "load_settings", _settings_without_env_file)
    clean_env.setenv("PLATFORM_PROFILE", "aws")
    assert run_bronze.main(["--verify-only"]) == 1
    out = _last_json(capsys)
    assert out["mode"] == "error" and "LAKE_URI" in out["error"]


def _settings_without_env_file():
    # Settings from the cleaned environment only, ignoring any developer .env file.
    from agent_data.config import load_settings

    return load_settings(env_file=None)


def test_pick_companies_defaults_and_dedupes():
    assert len(run_bronze.pick_companies(None)) == 25
    picked = run_bronze.pick_companies(["aapl", "AAPL", "TSLA"])
    assert [(c.ticker, c.sector) for c in picked] == [("AAPL", "Information Technology"), ("TSLA", "unlisted")]


def test_build_client_maps_edgar_settings(clean_env):
    clean_env.setenv("EDGAR_CONTACT_EMAIL", "me@example.org")
    client = run_bronze.build_client(_settings_without_env_file())
    assert client.user_agent.endswith("me@example.org")


def test_dry_run_and_verify_only_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        run_bronze.parse_args(["--dry-run", "--verify-only"])


def test_exit_now_flushes_then_ends_the_process_with_the_code(monkeypatch):
    calls = []
    monkeypatch.setattr(run_bronze.os, "_exit", lambda code: calls.append(("exit", code)))
    monkeypatch.setattr(run_bronze.logging, "shutdown", lambda: calls.append(("logging", None)))
    run_bronze.exit_now(1)
    assert calls == [("logging", None), ("exit", 1)]


def test_module_entry_point_exits_cleanly_in_a_real_subprocess(tmp_path):
    # End to end: python -m ... must return promptly with the JSON line and the right exit code.
    import os
    import subprocess
    import sys

    env = {**os.environ, "LAKE_URI": str(tmp_path / "lake"), "PLATFORM_PROFILE": "local"}
    result = subprocess.run([sys.executable, "-m", "agent_data.domains.edgar.run_bronze", "--verify-only"],
                            capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["mode"] == "verify"
