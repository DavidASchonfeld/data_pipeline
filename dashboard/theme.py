"""Shared Plotly dark theme — single source of truth for all chart styling.

Imports colors from design_tokens — update hex values there, not here.

Applied via **CHART_THEME in every update_layout() call so all dashboards share
a consistent dark aesthetic. Figure-specific kwargs passed after the spread
override any clashing keys (Plotly merges update_layout calls additively).

Previously duplicated verbatim between charts.py and weather_charts.py.
"""

# design_tokens.py is in the same /app directory — absolute 'dashboard.' prefix fails in Docker
from design_tokens import (
    BG_SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    BG_ELEVATED,
    BORDER,
    BORDER_LIGHT,
)

CHART_THEME = {
    "template":      "plotly_dark",         # plotly_dark provides sensible dark defaults
    "paper_bgcolor": BG_SURFACE,            # dark navy  — outer chart frame, matches --bg-surface
    "plot_bgcolor":  BG_SURFACE,            # dark navy  — inner plot area background
    "font": {
        # system-ui resolves to the native OS font — no external request needed
        "family": 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        "color":  TEXT_PRIMARY,             # soft white — primary text on dark background
        "size":   12,
    },
    "xaxis": {
        "gridcolor": BORDER,                # dark steel — subtle grid lines
        "linecolor": BORDER_LIGHT,          # slate      — axis spine line
        "tickcolor": BORDER_LIGHT,          # slate      — tick mark colour
        "tickfont":  {"color": TEXT_SECONDARY, "size": 11},  # cool gray — axis tick labels
        "zeroline":  False,                 # suppress the bold zero-line to reduce noise
    },
    "yaxis": {
        "gridcolor": BORDER,                # dark steel — horizontal grid lines
        "linecolor": BORDER_LIGHT,          # slate      — axis spine
        "tickcolor": BORDER_LIGHT,          # slate      — tick marks
        "tickfont":  {"color": TEXT_SECONDARY, "size": 11},  # cool gray — axis tick labels
        "zeroline":  False,
    },
    "legend": {
        "bgcolor":     BG_ELEVATED,         # dark slate-blue — legend panel background
        "bordercolor": BORDER,              # dark steel      — legend panel border
        "borderwidth": 1,
        "font":        {"color": TEXT_PRIMARY, "size": 12},  # soft white — legend text
    },
    "margin": {"l": 60, "r": 20, "t": 50, "b": 50},  # breathing room around the plot area
    "hoverlabel": {
        "bgcolor":    BG_ELEVATED,          # dark slate-blue — tooltip background
        "bordercolor": BORDER_LIGHT,        # slate           — tooltip border
        "font": {
            "color":  TEXT_PRIMARY,         # soft white — tooltip text
            "family": 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            "size":   12,
        },
    },
}
