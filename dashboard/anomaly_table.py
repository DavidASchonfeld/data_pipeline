"""
Reusable sortable-table builders shared by the stocks and weather anomaly sections.

Keeping table-rendering logic here lets callbacks.py stay focused on wiring,
and lets both dashboards share a single source of truth for column definitions.
"""

import pandas as pd
from dash import html


# ── Severity helper ───────────────────────────────────────────────────────────

def _severity_label(z: float) -> str:
    """Plain-language severity from z-score — non-technical words instead of raw numbers."""
    if z >= 3.0:
        return f"Extreme ({z:.1f})"
    if z >= 2.5:
        return f"Very Unusual ({z:.1f})"
    return f"Unusual ({z:.1f})"


# ── Legend skip-sets — these are shape-key decoration entries, not real names ─

STOCKS_SKIP  = frozenset({"Normal (○)", "Anomaly (✕)"})
WEATHER_SKIP = frozenset({"Normal (○)", "Anomaly (✕)"})


# ── Column definitions: (display label, DataFrame key, cell formatter) ────────

STOCKS_COLS = [
    ("Ticker",           "ticker",             str),
    ("Fiscal Year",      "fiscal_year",        str),
    ("Revenue YoY%",     "revenue_yoy_pct",    lambda v: f"{v:.1f}%"),
    ("Net Income YoY%",  "net_income_yoy_pct", lambda v: f"{v:.1f}%"),
    ("Anomaly?",         "is_anomaly",         lambda v: "Yes" if v else "No"),
    ("Score",            "anomaly_score",      lambda v: f"{v:.3f}"),
]

# Severity column sorts by z_score (numeric); display is a plain-language label
WEATHER_COLS = [
    ("City",               "city_name",        str),
    ("Date / Time",        "observation_time", lambda v: pd.to_datetime(v).strftime("%Y-%m-%d %H:%M") if pd.notna(v) else "N/A"),
    ("Temp (°F)",          "temperature_f",    lambda v: f"{v:.1f}"),
    ("City Avg (°F)",      "city_mean",        lambda v: f"{v:.1f}"),
    ("Diff from Avg (°F)", "deviation",        lambda v: f"{v:+.1f}"),
    ("Severity",           "z_score",          _severity_label),
]


# ── Visibility helper ─────────────────────────────────────────────────────────

def extract_color_map(figure: dict, skip_names: frozenset) -> dict:
    """Read each trace's color from the Plotly figure dict → {entity_name: hex_color}.

    Skips shape-key entries and guards against per-point color arrays (which are
    lists, not strings) so only one scalar color per entity is returned.
    """
    if not figure or "data" not in figure:
        return {}
    color_map = {}
    for t in figure["data"]:
        name = t.get("name", "")
        if name in skip_names:
            continue  # skip shape-key decoration entries (Normal ○, Anomaly ✕)
        color = t.get("marker", {}).get("color")
        if isinstance(color, str):  # per-point color arrays are lists — only take scalar strings
            color_map[name] = color
    return color_map


def _color_dot_cell(color: str | None) -> html.Td:
    """Narrow table cell with a filled circle matching the entity's Plotly legend color."""
    return html.Td(
        html.Span(style={
            "display": "inline-block",
            "width": "10px",
            "height": "10px",
            "borderRadius": "50%",
            "backgroundColor": color or "#8892a4",  # fall back to muted gray when color is unknown
        }),
        className="color-dot-cell",
    )


def get_visible_entities(figure: dict, skip_names: frozenset) -> list | None:
    """
    Inspect a Plotly figure dict and return the names of currently-visible traces.

    Plotly sets trace["visible"] = "legendonly" when the user clicks a legend entry
    to hide it.  Returns None when nothing is hidden (caller should show all rows).
    skip_names: trace names that are shape-key decorations (not real companies/cities).
    """
    if not figure or "data" not in figure:
        return None  # no figure yet — show everything

    # Only look at real entity traces, not the shape-key legend entries
    real_traces = [t for t in figure["data"] if t.get("name", "") not in skip_names]

    # Nothing hidden → no filtering needed
    if not any(t.get("visible") == "legendonly" for t in real_traces):
        return None

    # Return names of traces that are still shown
    return [t.get("name", "") for t in real_traces if t.get("visible") != "legendonly"]


# ── Stocks table rows ─────────────────────────────────────────────────────────

def build_stocks_table_rows(
    df: pd.DataFrame,
    sort_col: str | None,
    sort_dir: str,
    visible_tickers: list | None,
    color_map: dict | None = None,  # {ticker: hex} extracted from the scatter figure — drives the dot color
) -> list:
    """
    Return a list of html.Tr elements for the stocks anomaly table.
    Applies legend-visibility filter then user-selected column sort.
    """
    if df is None or df.empty:
        return [html.Tr([html.Td(
            "No data yet — run the pipeline first.",
            colSpan=len(STOCKS_COLS) + 1,  # +1 accounts for the color dot column
            style={"textAlign": "center", "color": "#8892a4"},
        )])]

    # Filter to only the tickers currently visible in the graph legend
    if visible_tickers is not None:
        df = df[df["ticker"].isin(visible_tickers)]

    # Apply user-selected column sort (None = keep original SQL order: anomalies first)
    if sort_col and sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=(sort_dir == "asc"))

    if df.empty:
        return [html.Tr([html.Td(
            "All companies hidden — click a name in the chart legend to show them.",
            colSpan=len(STOCKS_COLS) + 1,  # +1 accounts for the color dot column
            style={"textAlign": "center", "color": "#8892a4"},
        )])]

    rows = []
    for _, row in df.iterrows():
        dot   = _color_dot_cell((color_map or {}).get(row.get("ticker")))  # colored circle matching the chart legend
        cells = [dot] + [html.Td("" if row.get(key) is None else fmt(row.get(key)))
                         for _, key, fmt in STOCKS_COLS]
        cells.append(html.Td("", className="sort-clear-cell"))  # placeholder td so the × header column stays aligned
        # Red-tint row class for flagged anomalies — matches existing row-anomaly CSS
        css = "row-anomaly" if bool(row.get("is_anomaly", False)) else ""
        rows.append(html.Tr(cells, className=css))
    return rows


# ── Weather table rows ────────────────────────────────────────────────────────

def build_weather_table_rows(
    df: pd.DataFrame,
    sort_col: str | None,
    sort_dir: str,
    visible_cities: list | None,
    color_map: dict | None = None,  # {city_name: hex} extracted from the scatter figure — drives the dot color
) -> list:
    """
    Return a list of html.Tr elements for the weather anomaly table (flagged rows only).
    Applies legend-visibility filter then user-selected column sort.
    Default sort is most-extreme first (z_score descending) when no column is selected.
    """
    if df is None or df.empty or "is_anomaly" not in df.columns:
        return [html.Tr([html.Td(
            "No data yet — run the pipeline first.",
            colSpan=len(WEATHER_COLS) + 1,  # +1 accounts for the color dot column
            style={"textAlign": "center", "color": "#8892a4"},
        )])]

    # Weather table shows only flagged readings (not all 1 600+ hourly rows)
    df = df[df["is_anomaly"]].copy()

    # Filter to cities currently visible in the graph legend
    if visible_cities is not None:
        df = df[df["city_name"].isin(visible_cities)]

    # User sort overrides default; default = most extreme first
    if sort_col and sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=(sort_dir == "asc"))
    else:
        df = df.sort_values("z_score", ascending=False)

    if df.empty:
        return [html.Tr([html.Td(
            "No anomalies to display for the selected cities.",
            colSpan=len(WEATHER_COLS) + 1,  # +1 accounts for the color dot column
            style={"textAlign": "center", "color": "#8892a4"},
        )])]

    rows = []
    for _, row in df.iterrows():
        dot   = _color_dot_cell((color_map or {}).get(row.get("city_name")))  # colored circle matching the chart legend
        cells = [dot] + [html.Td("" if row.get(key) is None else fmt(row.get(key)))
                         for _, key, fmt in WEATHER_COLS]
        cells.append(html.Td("", className="sort-clear-cell"))  # placeholder td so the × header column stays aligned
        rows.append(html.Tr(cells, className="row-anomaly"))  # all rows here are anomalies
    return rows
