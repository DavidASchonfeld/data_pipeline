"""Tests for compute_weather_anomalies in dashboard/weather_charts.py — pure pandas, no external calls."""

import sys
from unittest.mock import MagicMock

import pandas as pd

# Stub snowflake packages — weather_charts imports chart_utils which may trigger config
for _mod in ["snowflake", "snowflake.sqlalchemy"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub dash so weather_charts (which imports dash.html) can be imported without a full install
import types as _types
_dash = _types.ModuleType("dash")
_dash_html = _types.ModuleType("dash.html")
_dash_html.P = MagicMock
_dash.html = _dash_html
sys.modules.setdefault("dash",      _dash)
sys.modules.setdefault("dash.html", _dash_html)

from weather_charts import compute_weather_anomalies

# Expected columns added by the function
_ANOMALY_COLS = ["city_mean", "city_std", "deviation", "z_score", "is_anomaly"]


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_weather_df(city: str, temperatures: list) -> pd.DataFrame:
    """Build a minimal weather DataFrame with city_name, observation_time, temperature_f."""
    n = len(temperatures)
    return pd.DataFrame({
        "city_name":       [city] * n,
        "observation_time": pd.date_range("2024-01-01", periods=n, freq="h"),
        "temperature_f":   temperatures,
    })


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_empty_df_returns_anomaly_columns():
    """Empty input df is returned with all five anomaly columns present."""
    result = compute_weather_anomalies(pd.DataFrame())
    for col in _ANOMALY_COLS:
        assert col in result.columns, f"Missing column: {col}"


def test_constant_temp_z_score_is_zero():
    """Constant temperature gives z_score=0.0 for every row (no NaN, no division-by-zero)."""
    df = _make_weather_df("Chicago", [70.0] * 10)
    result = compute_weather_anomalies(df)
    assert result["z_score"].isna().sum() == 0          # must never produce NaN
    assert (result["z_score"] == 0.0).all()              # std=0 path must yield 0.0, not NaN


def test_constant_temp_no_anomalies():
    """Constant temperature produces no anomaly flags."""
    df = _make_weather_df("Chicago", [70.0] * 10)
    result = compute_weather_anomalies(df)
    assert result["is_anomaly"].sum() == 0


def test_detects_extreme_outlier():
    """9 normal readings + 1 extreme outlier at 150°F should flag at least 1 anomaly."""
    temps = [70.0] * 9 + [150.0]  # 150°F is >20 standard deviations above the mean
    df = _make_weather_df("Chicago", temps)
    result = compute_weather_anomalies(df, z_threshold=2.0)
    assert result["is_anomaly"].sum() >= 1


def test_normal_readings_not_flagged():
    """Tightly clustered temperatures (70.0 ± 0.5°F) produce zero anomalies."""
    import random
    random.seed(42)
    temps = [70.0 + random.uniform(-0.5, 0.5) for _ in range(20)]
    df = _make_weather_df("Chicago", temps)
    result = compute_weather_anomalies(df, z_threshold=2.0)
    assert result["is_anomaly"].sum() == 0


def test_independent_per_city():
    """Each city's z-scores are computed against its own mean — Phoenix's stable 90°F readings
    are not flagged just because they are far from Chicago's 70°F mean."""
    chicago = _make_weather_df("Chicago", [70.0] * 10)
    phoenix = _make_weather_df("Phoenix", [90.0] * 10)  # different base temp, equally stable
    df = pd.concat([chicago, phoenix], ignore_index=True)
    result = compute_weather_anomalies(df, z_threshold=2.0)
    # Phoenix rows should not be flagged — their z_score within Phoenix is 0
    phoenix_rows = result[result["city_name"] == "Phoenix"]
    assert phoenix_rows["is_anomaly"].sum() == 0
