import pandas as pd
import plotly.graph_objects as go
from dash import html

from theme import CHART_THEME as _CHART_THEME  # shared dark theme — keeps both dashboards in sync
from chart_utils import build_color_map, anomaly_symbols, make_empty_figure  # shared helpers — avoids duplicating across chart modules
# ─────────────────────────────────────────────────────────────────────────────


def build_revenue_net_income_fig(ticker: str, revenue_df: pd.DataFrame, net_income_df: pd.DataFrame) -> go.Figure:
    """Grouped bar chart: Revenue + Net Income side-by-side per fiscal year.

    Values divided by 1e9 to display in billions (e.g. 394_328_000_000 → 394.33).
    barmode="group" places bars for the same x position next to each other (not stacked).
    """
    # Guard: show a themed placeholder when both series are empty (e.g. transient Snowflake miss)
    if revenue_df.empty and net_income_df.empty:
        return make_empty_figure(f"No data yet for {ticker}")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=revenue_df["fiscal_year"],
        y=revenue_df["value"] / 1e9,
        name="Revenue",
        marker_color="#3b82f6",  # cornflower blue
    ))
    fig.add_trace(go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",  # emerald green
    ))
    # Spread the shared dark theme first, then override with chart-specific keys
    fig.update_layout(
        **_CHART_THEME,
        title=f"{ticker} — Annual Revenue & Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
        barmode="group",
    )
    return fig


def build_net_income_fig(ticker: str, net_income_df: pd.DataFrame) -> go.Figure:
    """Standalone net income bar chart.

    Separate chart lets the user compare net income magnitude without Revenue dwarfing it.
    """
    # Guard: show a themed placeholder when the series is empty (e.g. transient Snowflake miss)
    if net_income_df.empty:
        return make_empty_figure(f"No data yet for {ticker}")
    fig = go.Figure(data=[go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",  # emerald green
    )])
    # Apply shared theme then add chart-specific title/axis labels
    fig.update_layout(
        **_CHART_THEME,
        title=f"{ticker} — Annual Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
    )
    return fig


def build_stats_table(ticker: str, revenue_df: pd.DataFrame, net_income_df: pd.DataFrame) -> html.Table:
    """Summary stats table: latest annual Revenue and Net Income for the selected ticker.

    html.Table / html.Thead / html.Tbody / html.Tr / html.Th / html.Td are
    Dash HTML components that map directly to standard HTML tags.
    """
    # Pull the most recent annual row for each metric (last row after ORDER BY period_end ASC)
    latest_rev  = revenue_df.iloc[-1]    if len(revenue_df)    > 0 else None
    latest_ni   = net_income_df.iloc[-1] if len(net_income_df) > 0 else None
    latest_year = latest_rev["fiscal_year"] if latest_rev is not None else "N/A"
    rev_str = f"${latest_rev['value']/1e9:.2f}B"  if latest_rev is not None else "N/A"
    ni_str  = f"${latest_ni['value']/1e9:.2f}B"   if latest_ni  is not None else "N/A"

    return html.Table(
        className="dash-table",  # CSS class provides dark background, borders, and layout
        children=[
            # ── Header row ──────────────────────────────────────────────
            html.Thead(html.Tr([html.Th(c)
                for c in ["Ticker", "Latest Fiscal Year", "Revenue", "Net Income"]
            ])),
            # ── Data row ────────────────────────────────────────────────
            html.Tbody(html.Tr([html.Td(v)
                for v in [ticker, latest_year, rev_str, ni_str]
            ])),
        ]
    )


# ── Anomaly detection charts ──────────────────────────────────────────────────

def build_anomaly_scatter(df: pd.DataFrame) -> go.Figure:
    """Scatter of Revenue YoY% vs Net Income YoY%, dual-encoded: color = company, shape = anomaly status.

    Circle = normal, x = anomaly. Two invisible shape-key traces document the convention in the legend.
    """
    # Guard: return a themed empty figure before the first DAG run populates the table
    if df.empty:
        return make_empty_figure("No data yet")

    tickers = sorted(df["ticker"].unique())   # sort for deterministic color assignment across refreshes
    color_map = build_color_map(tickers)      # one distinct color per company

    fig = go.Figure()

    # One trace per company — color encodes identity, symbol per-point encodes anomaly status
    for ticker in tickers:
        sub = df[df["ticker"] == ticker].sort_values("is_anomaly")  # normals first so legend swatch shows circle
        fig.add_trace(go.Scatter(
            x=sub["revenue_yoy_pct"],
            y=sub["net_income_yoy_pct"],
            mode="markers",
            name=ticker,
            marker={
                "color": color_map[ticker],                        # company color
                "size": 9,
                "symbol": anomaly_symbols(sub["is_anomaly"]),      # circle or x per point
            },
            customdata=sub[["ticker", "fiscal_year", "anomaly_score"]].values,  # supplies hover fields
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "FY: %{customdata[1]}<br>"
                "Score: %{customdata[2]:.3f}<br>"
                "Rev YoY: %{x:.1f}%<br>"
                "NI YoY: %{y:.1f}%"
                "<extra></extra>"                                  # suppresses the trace-name box
            ),
        ))

    # Two invisible shape-key entries so the legend documents circle=normal / x=anomaly
    for label, sym in [("Normal (○)", "circle"), ("Anomaly (✕)", "x")]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None],                                    # no data — legend display only
            mode="markers",
            name=label,
            marker={"symbol": sym, "size": 9, "color": "#8892a4"},  # cool gray — neutral legend swatch
            showlegend=True,
        ))

    # Apply shared dark theme then add chart-specific title/axis labels
    fig.update_layout(
        **_CHART_THEME,
        title="Anomaly Detection — YoY Growth",
        xaxis_title="Revenue YoY %",   # horizontal axis = revenue year-over-year growth
        yaxis_title="Net Income YoY %",  # vertical axis = net income year-over-year growth
    )
    return fig


# build_anomaly_table removed — table rendering moved to anomaly_table.py
# and wired via the render_anomaly_table callback in callbacks.py
# ─────────────────────────────────────────────────────────────────────────────


# ── Pipeline health table ─────────────────────────────────────────────────────

def build_health_table(df: pd.DataFrame):
    """HTML table: Table name, row count, latest record timestamp, age in hours.

    Amber background when age_hours > 25 for Financials/Anomalies (daily DAG),
    or > 2 for Weather (hourly DAG).
    Returns html.P placeholder when no data is available.
    """
    # Guard: show a friendly message before any pipeline run populates the tables
    if df.empty:
        return html.P("No pipeline health data yet — run the pipeline first.")

    header_cols = ["Table", "Row Count", "Latest Record", "Age (hours)"]
    header = html.Thead(html.Tr([
        html.Th(c) for c in header_cols  # CSS .dash-table th handles all header cell styling
    ]))

    # Compute age relative to current UTC time (tz-naive to match Snowflake TIMESTAMP_NTZ)
    now = pd.Timestamp.utcnow().tz_localize(None)
    rows = []
    for _, row in df.iterrows():
        latest_ts = pd.to_datetime(row["latest_ts"])  # ensure datetime type for arithmetic
        age_hours = (now - latest_ts).total_seconds() / 3600 if pd.notna(latest_ts) else None

        # Amber alert threshold differs by table — daily tables allow up to 25h, hourly allows 2h
        threshold = 2 if row["table_name"] == "Weather" else 25
        stale = age_hours is not None and age_hours > threshold
        # CSS class row-stale applies translucent amber tint; empty string = no override
        row_class = "row-stale" if stale else ""

        age_str = f"{age_hours:.1f}" if age_hours is not None else "N/A"
        ts_str  = latest_ts.strftime("%Y-%m-%d %H:%M") if pd.notna(latest_ts) else "N/A"
        cells = [
            html.Td(row["table_name"]),
            html.Td(f"{int(row['row_count']):,}"),  # comma-formatted for readability
            html.Td(ts_str),
            html.Td(age_str),
        ]
        rows.append(html.Tr(cells, className=row_class))  # className drives conditional row colour

    return html.Table(
        className="dash-table",  # CSS class provides dark surface, borders, and alternating stripes
        children=[header, html.Tbody(rows)],
    )
# ─────────────────────────────────────────────────────────────────────────────
