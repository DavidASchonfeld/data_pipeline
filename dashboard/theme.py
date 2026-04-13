"""Shared Plotly dark theme — single source of truth for all chart styling.

Applied via **CHART_THEME in every update_layout() call so all dashboards share
a consistent dark aesthetic. Figure-specific kwargs passed after the spread
override any clashing keys (Plotly merges update_layout calls additively).

Previously duplicated verbatim between charts.py and weather_charts.py.
"""

CHART_THEME = {
    "template":      "plotly_dark",         # plotly_dark provides sensible dark defaults
    "paper_bgcolor": "#1a1d27",             # dark navy  — outer chart frame, matches --bg-surface
    "plot_bgcolor":  "#1a1d27",             # dark navy  — inner plot area background
    "font": {
        # system-ui resolves to the native OS font — no external request needed
        "family": 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        "color":  "#e2e8f0",                # soft white — primary text on dark background
        "size":   12,
    },
    "xaxis": {
        "gridcolor": "#2d3348",             # dark steel — subtle grid lines
        "linecolor": "#374160",             # slate      — axis spine line
        "tickcolor": "#374160",             # slate      — tick mark colour
        "tickfont":  {"color": "#8892a4", "size": 11},  # cool gray — axis tick labels
        "zeroline":  False,                 # suppress the bold zero-line to reduce noise
    },
    "yaxis": {
        "gridcolor": "#2d3348",             # dark steel — horizontal grid lines
        "linecolor": "#374160",             # slate      — axis spine
        "tickcolor": "#374160",             # slate      — tick marks
        "tickfont":  {"color": "#8892a4", "size": 11},  # cool gray — axis tick labels
        "zeroline":  False,
    },
    "legend": {
        "bgcolor":     "#222638",           # dark slate-blue — legend panel background
        "bordercolor": "#2d3348",           # dark steel      — legend panel border
        "borderwidth": 1,
        "font":        {"color": "#e2e8f0", "size": 12},  # soft white — legend text
    },
    "margin": {"l": 60, "r": 20, "t": 50, "b": 50},  # breathing room around the plot area
    "hoverlabel": {
        "bgcolor":    "#222638",            # dark slate-blue — tooltip background
        "bordercolor": "#374160",           # slate           — tooltip border
        "font": {
            "color":  "#e2e8f0",            # soft white — tooltip text
            "family": 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            "size":   12,
        },
    },
}
