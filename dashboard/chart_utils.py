"""Shared chart helpers — reusable across the stocks and weather dashboards."""

import plotly.graph_objects as go
import plotly.colors as pc  # qualitative palette for distinct per-entity colors

from theme import CHART_THEME as _CHART_THEME  # shared dark theme — keeps all charts in sync


def build_color_map(labels: list) -> dict:
    """Assign each label a distinct color from the Plotly qualitative palette."""
    # Modulo wraps gracefully if there are ever more than 10 entities
    palette = pc.qualitative.Plotly
    return {label: palette[i % len(palette)] for i, label in enumerate(labels)}


def anomaly_symbols(is_anomaly_col) -> list:
    """Map boolean anomaly flags to marker shapes: x for anomalies, circle for normal."""
    return ["x" if v else "circle" for v in is_anomaly_col]  # shape encodes anomaly status


def make_empty_figure(message: str) -> go.Figure:
    """Return a dark-themed empty figure with a centered placeholder message."""
    # Prevents a jarring white panel when data has not yet arrived from the pipeline
    fig = go.Figure()
    fig.update_layout(**_CHART_THEME)
    fig.add_annotation(
        text=message, showarrow=False,
        font={"size": 14, "color": "#8892a4"},  # cool gray — muted placeholder text
    )
    return fig
