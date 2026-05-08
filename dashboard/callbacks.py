import logging
import pandas as pd  # needed to parse JSON data from dcc.Store back into DataFrames
from dash import html, ClientsideFunction  # ClientsideFunction wires JS functions to Dash callbacks
from dash.dependencies import Input, Output, State  # State reads values without triggering callbacks

logger = logging.getLogger(__name__)  # module-level logger — writes to pod stdout (visible in kubectl logs)

from db import _load_ticker_data, load_anomalies, load_weather_data, load_stock_health, load_weather_health  # split health loaders: stocks page uses load_stock_health, weather page uses load_weather_health
from charts import build_revenue_net_income_fig, build_net_income_fig, build_stats_table, build_anomaly_scatter, build_health_table  # build_health_table added for the health panel
from chart_utils import make_empty_figure  # shared themed empty-figure helper — keeps fallback figures on-brand
from weather_charts import build_temperature_fig, build_weather_stats_table, compute_weather_anomalies, build_weather_anomaly_scatter  # weather chart builders; anomaly functions added for anomaly detection section
from security import ALLOWED_TICKERS, ALLOWED_CITIES  # import centralised allowlists — avoids duplicating the sets here
from anomaly_table import (  # shared table logic so both dashboards use identical column definitions
    STOCKS_COLS, WEATHER_COLS, STOCKS_SKIP, WEATHER_SKIP,
    get_visible_entities, build_stocks_table_rows, build_weather_table_rows,
    extract_color_map,  # reads hex colors from the live figure dict for the dot column
)

# ── Pre-compute stable output lists so the decorators below stay readable ─────
# One output for the table body + one per column for sort indicators + classNames
_STOCKS_KEYS = [key for _, key, _ in STOCKS_COLS]
_WEATHER_KEYS = [key for _, key, _ in WEATHER_COLS]

_ANOM_TABLE_OUTPUTS = (
    [Output("anomaly-table-body", "children")] +
    [Output(f"anom-sort-ind-{k}", "children") for k in _STOCKS_KEYS] +
    [Output(f"anom-col-{k}", "className") for k in _STOCKS_KEYS] +
    [Output("anomaly-table", "className")]  # toggles sort-active class to show/hide the × clear button
)

_WANOM_TABLE_OUTPUTS = (
    [Output("weather-anomaly-table-body", "children")] +
    [Output(f"wanom-sort-ind-{k}", "children") for k in _WEATHER_KEYS] +
    [Output(f"wanom-col-{k}", "className") for k in _WEATHER_KEYS] +
    [Output("weather-anomaly-table", "className")]  # toggles sort-active class to show/hide the × clear button
)


def _sort_header_outputs(sort_state: dict, col_keys: list) -> tuple[list, list]:
    """
    Compute sort-indicator glyphs and header class names from the current sort state.
    Returns (indicators, class_names) — each a list aligned with col_keys.
    """
    sort_col = (sort_state or {}).get("column")
    sort_dir = (sort_state or {}).get("direction", "asc")
    indicators, classes = [], []
    for key in col_keys:
        if key == sort_col:
            indicators.append("▲" if sort_dir == "asc" else "▼")
            classes.append("sortable-header sorted")
        else:
            indicators.append("")
            classes.append("sortable-header")
    return indicators, classes


def register_callbacks(dash_app) -> None:
    """Register all Dash callbacks onto the given Dash app instance."""

    @dash_app.callback(
        Output("price-chart", "figure"),    # 1st return value → sets the Revenue+NetIncome grouped bar chart
        Output("volume-chart", "figure"),   # 2nd return value → sets the Net Income standalone bar chart
        Output("stats-table", "children"),  # 3rd return value → sets the stats table's HTML children
        Input("ticker-dropdown", "value"),  # triggers callback when the dropdown selection changes
    )
    def update_charts(ticker: str):
        """Re-render all three outputs whenever the user picks a different ticker.

        Dash callbacks are reactive: Dash automatically calls this function whenever
        any Input component changes. The return values are mapped positionally to the
        Output components defined above — order matters.
        """
        if ticker not in ALLOWED_TICKERS:                                                              # reject unknown tickers before any DB call — prevents strangers cache-busting Snowflake queries
            empty_fig = make_empty_figure("Invalid ticker")                                            # themed empty figure keeps the dashboard on-brand even in the error path
            return empty_fig, empty_fig, html.P("Invalid ticker selection.", style={"color": "red"})   # return all three outputs so Dash's positional mapping doesn't raise a mismatch error
        try:
            df = _load_ticker_data(ticker)
        except Exception:
            logger.exception("Failed to load ticker data for %s", ticker)  # full traceback to pod stdout
            empty_fig = make_empty_figure("Data temporarily unavailable")  # themed placeholder instead of raw unstyled Plotly figure
            return empty_fig, empty_fig, html.P("Data temporarily unavailable. Please try again later.", style={"color": "red"})

        # Split into per-metric DataFrames for separate traces
        revenue_df    = df[df["metric"] == "Revenues"].copy()
        net_income_df = df[df["metric"] == "NetIncomeLoss"].copy()

        price_fig  = build_revenue_net_income_fig(ticker, revenue_df, net_income_df)
        volume_fig = build_net_income_fig(ticker, net_income_df)
        stats      = build_stats_table(ticker, revenue_df, net_income_df)

        return price_fig, volume_fig, stats

    # ── Anomaly Detection — data loading callback ─────────────────────────────
    # Pushes the scatter figure + raw JSON data to the store; table renders separately
    @dash_app.callback(
        Output("anomaly-scatter", "figure"),       # updates the scatter plot figure
        Output("anomaly-data-store", "data"),      # stores anomaly data as JSON for the table callback
        Input("anomaly-refresh-btn", "n_clicks"),  # triggers on button click; also fires on initial page load
        prevent_initial_call=False,  # load data immediately on page load, not just on button click
    )
    def update_anomalies(n_clicks):
        """Push anomaly data to the store on page load or Refresh click; table re-renders reactively."""
        try:
            df = load_anomalies()  # query Snowflake (or return empty frame for non-Snowflake backends)
        except Exception:
            logger.exception("Failed to load anomaly data")  # full traceback to pod stdout
            empty_fig = make_empty_figure("Data temporarily unavailable")  # themed placeholder instead of raw unstyled Plotly figure
            return empty_fig, None  # None clears the store so the table shows its empty-state message
        # Serialize to JSON so the table callback can re-render on legend clicks without re-querying
        return build_anomaly_scatter(df), df.to_json(orient="records", date_format="iso")

    # ── Anomaly Detection — table rendering callback ──────────────────────────
    # Fires when data loads, when the graph legend is toggled, or when a sort column changes
    @dash_app.callback(
        *_ANOM_TABLE_OUTPUTS,
        Input("anomaly-data-store", "data"),       # fires when fresh data arrives
        Input("anomaly-scatter", "restyleData"),    # fires when the user hides/shows a trace via legend
        Input("anomaly-sort-state", "data"),        # fires when the user changes the sort column
        State("anomaly-scatter", "figure"),         # read current trace visibility without triggering callback
        prevent_initial_call=True,  # wait for the data store to be populated before first render
    )
    def render_anomaly_table(data_json, restyle, sort_state, figure):
        """Re-render table rows filtered by graph legend visibility and sorted by user-selected column."""
        sort_col = (sort_state or {}).get("column")
        sort_dir = (sort_state or {}).get("direction", "asc")

        # Parse stored JSON back into a DataFrame; guard against empty store
        df = pd.read_json(data_json, orient="records") if data_json else pd.DataFrame()

        # Determine which tickers are currently visible in the graph legend
        visible   = get_visible_entities(figure, STOCKS_SKIP)
        color_map = extract_color_map(figure, STOCKS_SKIP)  # {ticker: hex} for the dot column

        # Build filtered + sorted table rows (with color dots)
        rows = build_stocks_table_rows(df, sort_col, sort_dir, visible, color_map)

        # Compute sort-indicator glyphs and header class names
        indicators, classes = _sort_header_outputs(sort_state, _STOCKS_KEYS)

        # sort-active class shows the × button when a column is sorted; plain class hides it
        table_class = "dash-table sort-active" if sort_col else "dash-table"

        # Return value order must match _ANOM_TABLE_OUTPUTS: body, indicators..., classes..., table className
        return [rows] + indicators + classes + [table_class]

    # ── Anomaly Detection — clientside sort state (column click + × clear button) ─
    # Runs in the browser via anomaly_sort.js — no server round-trip needed
    dash_app.clientside_callback(
        ClientsideFunction(namespace="anomaly_sort", function_name="handleStocksSort"),
        Output("anomaly-sort-state", "data"),
        [Input(f"anom-col-{k}", "n_clicks") for k in _STOCKS_KEYS] + [Input("anom-sort-clear-btn", "n_clicks")],  # clear button wired as final input
        State("anomaly-sort-state", "data"),
    )

    # ── Pipeline Health callback ───────────────────────────────────────────────
    @dash_app.callback(
        Output("health-table", "children"),       # updates the pipeline health HTML table
        Input("anomaly-refresh-btn", "n_clicks"), # shares the existing Refresh button — no extra query trigger
        prevent_initial_call=False,  # populate on page load, not just on button click
    )
    def update_health(n_clicks):
        """Render stock pipeline health table (Financials + Anomalies only) on page load or refresh."""
        try:
            df = load_stock_health()  # stock-only health: Financials + Anomalies; weather has its own panel
        except Exception:
            logger.exception("Failed to load stock health")  # full traceback to pod stdout
            return html.P("Data temporarily unavailable. Please try again later.", style={"color": "red"})
        return build_health_table(df)  # renders row counts + freshness table


def register_weather_callbacks(weather_dash_app) -> None:
    """Register all Dash callbacks onto the weather Dash app instance."""

    @weather_dash_app.callback(
        Output("weather-temp-chart", "figure"),    # 1st return value → sets the temperature line chart
        Output("weather-stats-table", "children"), # 2nd return value → sets the stats table's HTML children
        Input("weather-refresh-btn", "n_clicks"),  # triggers on button click and on initial page load
        Input("city-dropdown", "value"),           # city selection — filters the cached full-dataset client-side
        prevent_initial_call=False,  # load data immediately on page load, not just on button click
    )
    def update_weather(n_clicks, city):
        """Re-render temperature chart and stats for selected city on page load, refresh, or city change."""
        if city not in ALLOWED_CITIES:  # validate against allowlist — prevents cache-busting with arbitrary strings
            empty_fig = make_empty_figure("Invalid city selection")  # themed placeholder instead of raw unstyled Plotly figure
            return empty_fig, html.P("Invalid city selection.", style={"color": "red"})
        try:
            df = load_weather_data()  # full dataset for all cities (cached 15 min); filter below
            if not df.empty and "city_name" in df.columns:
                df = df[df["city_name"] == city]  # filter to selected city — no extra Snowflake query needed
        except Exception:
            logger.exception("Failed to load weather data")  # full traceback to pod stdout
            empty_fig = make_empty_figure("Data temporarily unavailable")  # themed placeholder instead of raw unstyled Plotly figure
            return empty_fig, html.P("Data temporarily unavailable. Please try again later.", style={"color": "red"})
        return build_temperature_fig(df, city), build_weather_stats_table(df)  # chart + stats rendered from the filtered DataFrame

    # ── Weather Pipeline Health callback ──────────────────────────────────────
    @weather_dash_app.callback(
        Output("weather-health-table", "children"),          # weather-specific health panel on the weather page
        Input("weather-anomaly-refresh-btn", "n_clicks"),    # shares the Refresh Anomalies button — health is part of the anomaly section
        prevent_initial_call=False,  # populate on page load, not just on button click
    )
    def update_weather_health(n_clicks):
        """Render weather pipeline health table (Weather table only) on page load or refresh."""
        try:
            df = load_weather_health()  # weather-only health: just the FCT_WEATHER_HOURLY freshness row
        except Exception:
            logger.exception("Failed to load weather health")  # full traceback to pod stdout
            return html.P("Data temporarily unavailable. Please try again later.", style={"color": "red"})
        return build_health_table(df)  # renders row count + freshness for the weather table

    # ── Weather Anomaly Detection — data loading callback ────────────────────
    # Pushes scatter figure + computed anomaly JSON to the store; table renders separately
    @weather_dash_app.callback(
        Output("weather-anomaly-scatter", "figure"),         # scatter: temperature over time, anomalies highlighted
        Output("weather-anomaly-data-store", "data"),        # stores computed anomaly data as JSON for the table callback
        Input("weather-anomaly-refresh-btn", "n_clicks"),    # triggers on button click and page load
        prevent_initial_call=False,  # load anomaly data immediately on page load
    )
    def update_weather_anomalies(n_clicks):
        """Compute z-score anomalies and push to store on page load or refresh; table re-renders reactively."""
        try:
            df = load_weather_data()  # full dataset, all cities (cached 15 min) — no extra Snowflake query
        except Exception:
            logger.exception("Failed to load weather data for anomaly detection")  # full traceback to pod stdout
            empty_fig = make_empty_figure("Data temporarily unavailable")  # themed placeholder instead of raw unstyled Plotly figure
            return empty_fig, None  # None clears the store so the table shows its empty-state message
        df = compute_weather_anomalies(df)  # z-score per city, in-memory — no DB call
        # Serialize to JSON so the table callback can re-render on legend clicks without recomputing
        return build_weather_anomaly_scatter(df), df.to_json(orient="records", date_format="iso")

    # ── Weather Anomaly Detection — table rendering callback ─────────────────
    # Fires when data loads, when the graph legend is toggled, or when a sort column changes
    @weather_dash_app.callback(
        *_WANOM_TABLE_OUTPUTS,
        Input("weather-anomaly-data-store", "data"),       # fires when fresh data arrives
        Input("weather-anomaly-scatter", "restyleData"),   # fires when the user hides/shows a city via legend
        Input("weather-anomaly-sort-state", "data"),       # fires when the user changes the sort column
        State("weather-anomaly-scatter", "figure"),        # read current trace visibility without triggering callback
        prevent_initial_call=True,  # wait for the data store to be populated before first render
    )
    def render_weather_anomaly_table(data_json, restyle, sort_state, figure):
        """Re-render weather table rows filtered by graph legend visibility and sorted by user-selected column."""
        sort_col = (sort_state or {}).get("column")
        sort_dir = (sort_state or {}).get("direction", "asc")

        # Parse stored JSON back into a DataFrame; guard against empty store
        df = pd.read_json(data_json, orient="records") if data_json else pd.DataFrame()

        # Determine which cities are currently visible in the graph legend
        visible   = get_visible_entities(figure, WEATHER_SKIP)
        color_map = extract_color_map(figure, WEATHER_SKIP)  # {city: hex} for the dot column

        # Build filtered + sorted table rows (anomalous readings only, with color dots)
        rows = build_weather_table_rows(df, sort_col, sort_dir, visible, color_map)

        # Compute sort-indicator glyphs and header class names
        indicators, classes = _sort_header_outputs(sort_state, _WEATHER_KEYS)

        # sort-active class shows the × button when a column is sorted; plain class hides it
        table_class = "dash-table sort-active" if sort_col else "dash-table"

        # Return value order must match _WANOM_TABLE_OUTPUTS: body, indicators..., classes..., table className
        return [rows] + indicators + classes + [table_class]

    # ── Weather Anomaly Detection — clientside sort state (column click + × clear button) ─
    # Runs in the browser via anomaly_sort.js — no server round-trip needed
    weather_dash_app.clientside_callback(
        ClientsideFunction(namespace="anomaly_sort", function_name="handleWeatherSort"),
        Output("weather-anomaly-sort-state", "data"),
        [Input(f"wanom-col-{k}", "n_clicks") for k in _WEATHER_KEYS] + [Input("wanom-sort-clear-btn", "n_clicks")],  # clear button wired as final input
        State("weather-anomaly-sort-state", "data"),
    )
