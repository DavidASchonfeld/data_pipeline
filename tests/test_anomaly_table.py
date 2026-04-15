"""Tests for dashboard/anomaly_table.py — _severity_label, build_stocks_table_rows, build_weather_table_rows."""

import sys
import os
from unittest.mock import MagicMock

import pandas as pd

_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_DASHBOARD_DIR))

# Stub heavy optional deps before any dashboard import
for _mod in ["dotenv", "sqlalchemy", "pymysql"]:
    sys.modules.setdefault(_mod, MagicMock())
sys.modules["dotenv"].load_dotenv = MagicMock()

# Stub dash so anomaly_table can be imported without a full Dash install
import types as _types
_dash = _types.ModuleType("dash")
_dash_html = _types.ModuleType("dash.html")

class _HtmlComponent:
    """Minimal stand-in for Dash HTML components — stores tag name and children."""
    def __init__(self, *args, **kwargs):
        # First positional arg is children; keyword args capture style, className, etc.
        self.children = args[0] if args else kwargs.get("children")
        self.kwargs = kwargs

class _Td(_HtmlComponent): pass
class _Tr(_HtmlComponent): pass
class _Th(_HtmlComponent): pass
class _Span(_HtmlComponent): pass
class _P(_HtmlComponent): pass
class _Table(_HtmlComponent): pass
class _Thead(_HtmlComponent): pass
class _Tbody(_HtmlComponent): pass

_dash_html.Td    = _Td
_dash_html.Tr    = _Tr
_dash_html.Th    = _Th
_dash_html.Span  = _Span
_dash_html.P     = _P
_dash_html.Table = _Table
_dash_html.Thead = _Thead
_dash_html.Tbody = _Tbody
_dash.html = _dash_html
sys.modules.setdefault("dash",      _dash)
sys.modules.setdefault("dash.html", _dash_html)

from anomaly_table import _severity_label, build_stocks_table_rows, build_weather_table_rows
# Import html from our stub so isinstance checks in tests use the same class
from dash import html


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _stocks_df(*rows):
    """Build a stocks DataFrame from (ticker, fiscal_year, rev_yoy, ni_yoy, is_anomaly, score) tuples."""
    return pd.DataFrame(rows, columns=[
        "ticker", "fiscal_year", "revenue_yoy_pct", "net_income_yoy_pct",
        "is_anomaly", "anomaly_score",
    ])


def _weather_df(*rows):
    """Build a weather DataFrame from (city, time, temp, mean, dev, z, is_anomaly) tuples."""
    return pd.DataFrame(rows, columns=[
        "city_name", "observation_time", "temperature_f",
        "city_mean", "deviation", "z_score", "is_anomaly",
    ])


# ── _severity_label ──────────────────────────────────────────────────────────

def test_severity_label_extreme():
    """z=3.5 is above the Extreme threshold."""
    assert "Extreme" in _severity_label(3.5)


def test_severity_label_very_unusual():
    """z=2.7 falls in the Very Unusual band."""
    assert "Very Unusual" in _severity_label(2.7)


def test_severity_label_unusual():
    """z=2.1 is below both higher thresholds — classified as Unusual."""
    assert "Unusual" in _severity_label(2.1)
    assert "Extreme"     not in _severity_label(2.1)
    assert "Very Unusual" not in _severity_label(2.1)


def test_severity_label_boundary_3():
    """z=3.0 exactly meets the Extreme threshold (>=3.0)."""
    assert "Extreme" in _severity_label(3.0)


def test_severity_label_boundary_25():
    """z=2.5 exactly meets the Very Unusual threshold (>=2.5)."""
    label = _severity_label(2.5)
    assert "Very Unusual" in label
    assert "Extreme" not in label  # must not over-classify at exactly 2.5


# ── build_stocks_table_rows ───────────────────────────────────────────────────

def test_build_stocks_table_rows_empty_df():
    """Empty DataFrame returns a single placeholder row."""
    rows = build_stocks_table_rows(pd.DataFrame(), None, "desc", None)
    assert len(rows) == 1
    # Placeholder text lives inside the single Td child
    td_text = rows[0].children[0].children
    assert "No data" in td_text


def test_build_stocks_table_rows_two_rows():
    """2-row DataFrame produces exactly 2 html.Tr elements."""
    df = _stocks_df(
        ("AAPL", 2024, 8.0, 12.0, True,  -0.15),
        ("MSFT", 2024, 5.0,  6.0, False,  0.05),
    )
    rows = build_stocks_table_rows(df, None, "desc", None)
    assert len(rows) == 2
    assert all(isinstance(r, html.Tr) for r in rows)


def test_build_stocks_table_rows_visibility_filter():
    """visible_tickers=["AAPL"] on a 3-row df returns only the AAPL row."""
    df = _stocks_df(
        ("AAPL",  2024, 8.0, 12.0, True,  -0.15),
        ("MSFT",  2024, 5.0,  6.0, False,  0.05),
        ("GOOGL", 2024, 3.0,  4.0, False,  0.08),
    )
    rows = build_stocks_table_rows(df, None, "desc", ["AAPL"])
    assert len(rows) == 1


# ── build_weather_table_rows ──────────────────────────────────────────────────

def test_build_weather_table_rows_empty_df():
    """Empty DataFrame returns a single placeholder row."""
    rows = build_weather_table_rows(pd.DataFrame(), None, "desc", None)
    assert len(rows) == 1
    td_text = rows[0].children[0].children
    assert "No data" in td_text


def test_build_weather_table_rows_filters_to_anomalies():
    """Only the row with is_anomaly=True is returned; the normal row is excluded."""
    df = _weather_df(
        ("Chicago", "2024-01-01 12:00", 72.0, 70.0,  2.0, 2.8, True),   # anomaly
        ("Chicago", "2024-01-01 13:00", 70.5, 70.0,  0.5, 0.3, False),  # normal
    )
    rows = build_weather_table_rows(df, None, "desc", None)
    assert len(rows) == 1  # only the flagged reading makes it into the table
