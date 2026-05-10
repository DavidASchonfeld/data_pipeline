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


def make_loading_figure() -> go.Figure:
    """Shown while the initial Snowflake prewarm is in flight."""
    return make_empty_figure("Fetching latest data from Snowflake…")


def make_no_data_figure(hint: str = "") -> go.Figure:
    """Shown when Snowflake connected but returned zero rows — pipeline hasn't run yet."""
    msg = "Pipeline hasn't published data yet."
    if hint:
        msg += f" {hint}"
    return make_empty_figure(msg)


def _make_error_panel(headline: str, detail: str = "") -> go.Figure:
    """Friendly headline plus an optional second line showing the raw driver detail.

    The detail line is what makes the difference between "looks like a network issue"
    and "actually it's an MFA mandate" — we want both visible to a user reading the page.
    """
    fig = make_empty_figure(headline)
    if detail:
        # Anchor the detail line just below the headline; smaller + dimmer + monospace so it reads as technical context, not a second alert
        fig.add_annotation(
            text=detail, showarrow=False,
            yshift=-22,  # nudge below the headline annotation
            font={"size": 11, "color": "#6c7689", "family": "monospace"},
        )
    return fig


def make_error_figure(detail: str = "") -> go.Figure:
    """Shown when the Snowflake query raised an exception — temporary connection problem."""
    return _make_error_panel("Couldn't reach Snowflake — will retry automatically.", detail)


def make_account_suspended_figure(detail: str = "") -> go.Figure:
    """Shown when Snowflake rejects the connection because the trial ended or billing lapsed."""
    return _make_error_panel("Snowflake account suspended — check billing or trial status.", detail)


def make_bad_credentials_figure(detail: str = "") -> go.Figure:
    """Shown when Snowflake returns errno 390100 (wrong username/password)."""
    return _make_error_panel("Snowflake credentials rejected — check the K8s secret.", detail)


def make_network_error_figure(detail: str = "") -> go.Figure:
    """Shown when the Snowflake host is unreachable (errno 250001/250003)."""
    return _make_error_panel("Can't reach Snowflake servers — check network connectivity.", detail)
