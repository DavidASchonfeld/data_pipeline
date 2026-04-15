"""Tests for shared/gate_utils.py — _has_new_rows and check_daily_gate."""

import sys
import types
import importlib
from datetime import date
from unittest.mock import MagicMock, patch


# ── Minimal stubs so gate_utils imports without Airflow or file_logger installed ──
# file_logger stub — OutputTextWriter is used as a type annotation only in gate_utils
_file_logger = types.ModuleType("file_logger")
_file_logger.OutputTextWriter = MagicMock  # replace class with MagicMock so type hints resolve
sys.modules.setdefault("file_logger", _file_logger)

# Stub the airflow.sdk.Variable used inside check_daily_gate (deferred import)
_airflow_sdk = types.ModuleType("airflow.sdk")
_airflow_sdk.Variable = MagicMock()
sys.modules.setdefault("airflow", types.ModuleType("airflow"))
sys.modules.setdefault("airflow.sdk", _airflow_sdk)

# Ensure sys.path includes the dags directory so 'shared' is importable
import os
_DAGS_DIR = os.path.join(os.path.dirname(__file__), "..", "airflow", "dags")
if _DAGS_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_DAGS_DIR))

from shared.gate_utils import _has_new_rows, check_daily_gate  # module under test


# ── _has_new_rows ─────────────────────────────────────────────────────────────

def test_has_new_rows_positive():
    """Returns True when rows were written."""
    assert _has_new_rows(10) is True


def test_has_new_rows_zero():
    """Returns False when no rows written — ShortCircuitOperator should skip downstream."""
    assert _has_new_rows(0) is False


def test_has_new_rows_one():
    """Boundary: exactly one row is still truthy."""
    assert _has_new_rows(1) is True


# ── check_daily_gate ──────────────────────────────────────────────────────────

def _make_writer():
    """Return a MagicMock writer whose .log() calls are recorded."""
    return MagicMock()


def test_gate_skips_when_already_written_today():
    """Returns 0 (skip) when Variable equals today's ISO date."""
    today = date.today().isoformat()
    writer = _make_writer()

    # Variable is a deferred import inside check_daily_gate — patch where it's resolved
    with patch("airflow.sdk.Variable") as mock_var:
        mock_var.get.return_value = today  # simulate: gate was already set today
        result = check_daily_gate("SF_STOCKS_LAST_WRITE_DATE", writer)

    assert result == 0  # falsy — downstream tasks should be skipped
    writer.log.assert_called()  # gate should log its decision


def test_gate_proceeds_when_date_differs():
    """Returns 1 (proceed) when Variable holds a different date."""
    writer = _make_writer()

    # Variable is a deferred import inside check_daily_gate — patch where it's resolved
    with patch("airflow.sdk.Variable") as mock_var:
        mock_var.get.return_value = "2000-01-01"  # simulate: stale date from previous run
        result = check_daily_gate("SF_STOCKS_LAST_WRITE_DATE", writer)

    assert result == 1  # truthy — downstream tasks should run


def test_gate_proceeds_on_first_run():
    """Returns 1 (proceed) when Variable does not exist yet (first run)."""
    writer = _make_writer()

    # Variable is a deferred import inside check_daily_gate — patch where it's resolved
    # Variable.get uses default="" so a missing key returns "" (not KeyError) in Airflow 3.x
    with patch("airflow.sdk.Variable") as mock_var:
        mock_var.get.return_value = ""  # simulate: no variable set yet (first run)
        result = check_daily_gate("SF_STOCKS_LAST_WRITE_DATE", writer)

    assert result == 1  # first run should always proceed
