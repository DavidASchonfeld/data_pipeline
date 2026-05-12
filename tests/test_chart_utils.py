"""Tests for dashboard/chart_utils.py — build_color_map, anomaly_symbols, make_empty_figure."""

import plotly.graph_objects as go
from chart_utils import build_color_map, anomaly_symbols, make_empty_figure


# ── build_color_map ──────────────────────────────────────────────────────────

def test_build_color_map_empty():
    """Empty label list returns an empty dict."""
    assert build_color_map([]) == {}


def test_build_color_map_two_labels():
    """Two labels get distinct (non-identical) colors."""
    result = build_color_map(["AAPL", "MSFT"])
    assert result["AAPL"] != result["MSFT"]  # each entity must have its own color


def test_build_color_map_wraps_palette():
    """15 labels (>10) does not raise and all 15 labels appear in the result."""
    labels = [f"label_{i}" for i in range(15)]
    result = build_color_map(labels)
    assert len(result) == 15                  # every label must be present
    assert set(result.keys()) == set(labels)  # no label dropped or renamed


def test_build_color_map_deterministic():
    """Same input always produces identical output (order-stable assignment)."""
    labels = ["Chicago", "New York", "Phoenix"]
    assert build_color_map(labels) == build_color_map(labels)


# ── anomaly_symbols ──────────────────────────────────────────────────────────

def test_anomaly_symbols_empty():
    """Empty input returns an empty list."""
    assert anomaly_symbols([]) == []


def test_anomaly_symbols_mixed():
    """[True, False, True] maps to ['x', 'circle', 'x']."""
    assert anomaly_symbols([True, False, True]) == ["x", "circle", "x"]


def test_anomaly_symbols_all_false():
    """All-False input returns all 'circle' entries."""
    result = anomaly_symbols([False, False, False])
    assert result == ["circle", "circle", "circle"]


def test_anomaly_symbols_all_true():
    """All-True input returns all 'x' entries."""
    result = anomaly_symbols([True, True, True])
    assert result == ["x", "x", "x"]


# ── make_empty_figure ────────────────────────────────────────────────────────

def test_make_empty_figure_returns_figure():
    """Return value is a plotly go.Figure instance."""
    fig = make_empty_figure("Loading...")
    assert isinstance(fig, go.Figure)


def test_make_empty_figure_annotation():
    """The figure's annotation text matches the message argument exactly."""
    msg = "No data yet — run the pipeline first."
    fig = make_empty_figure(msg)
    # Plotly stores annotations as a tuple of dicts on the layout object
    annotations = fig.layout.annotations
    assert len(annotations) >= 1
    assert annotations[0].text == msg
