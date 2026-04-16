import dash
import flask
from dash import dcc, html
from flask import Flask

# ── Architecture: Why Flask + Dash together? ──────────────────────────────────
# Dash is a Python framework for interactive data dashboards built on top of
# Flask, React, and Plotly. Because Dash is built on Flask, a Dash app IS a
# Flask app — they share the same WSGI server (Gunicorn) and the same process.
#
# How the two frameworks are combined here:
#   1. Create a plain Flask `app` first.
#   2. Create a Dash `dash_app` that mounts ONTO the Flask app (server=app).
#   3. Dash registers its own routes under /dashboard/; Flask handles the rest.
#   4. Gunicorn is pointed at `app` (the Flask object), which already contains Dash.
# ─────────────────────────────────────────────────────────────────────────────

from routes import register_routes
from callbacks import register_callbacks, register_weather_callbacks  # weather callbacks added for the second Dash app
from db import prewarm_cache  # imported here to fire pre-warming without going through the callback layer
from security import init_security, ALLOWED_CITIES, ALLOWED_TICKERS  # centralised security — rate limiting, headers, CORS; allowlists for dropdowns
from config import FLASK_SECRET_KEY  # centralised dashboard configuration
from spot import build_spot_layout_components, register_spot_callbacks, build_offline_layout_components, register_offline_callbacks  # spot interruption + offline detection banners

app = Flask(__name__)

app.config["SECRET_KEY"] = FLASK_SECRET_KEY  # required for Flask sessions/cookies; loaded from K8s secret via config.py
if not app.config["SECRET_KEY"]:
    raise RuntimeError("FLASK_SECRET_KEY env var is not set — refusing to start without a secret key")  # fail fast rather than silently run insecure

init_security(app)  # attach rate limiting, security headers, and CORS — must run before routes are registered

# Dash mounted on the Flask server at /dashboard/
dash_app = dash.Dash(
    __name__,
    server=app,           # attach Dash to our existing Flask instance
    url_base_pathname="/dashboard/",
)

TICKERS = sorted(ALLOWED_TICKERS)  # single source of truth — derived from the security allowlist

dash_app.layout = html.Div(
    className="dash-page",  # CSS class handles max-width, centering, and padding
    children=[
        # Spot interruption banner: invisible intervals + fixed-position toast; no-op on non-EC2
        *build_spot_layout_components("stocks"),
        # Offline detection banner: JS health check every 15s — shows if the server goes down
        *build_offline_layout_components("stocks"),

        html.H1("Stock Market Analytics Pipeline"),  # color set globally in theme.css
        html.P(
            "SEC EDGAR financial data pulled daily via Airflow → Kafka → Snowflake.",
            className="dash-subtitle",  # CSS class applies muted color and bottom margin
        ),

        # Navigation link to the weather dashboard — wrapped in a nav div for layout and border
        html.Div(className="dash-nav", children=[
            html.A(
                "View Weather Dashboard →",
                href="/weather/",  # points to the weather Dash app mounted below
                className="dash-nav__link",  # CSS class styles this as a pill-button link
            ),
        ]),

        # ── Ticker selector ───────────────────────────────────────────────
        html.Label("Select Ticker:"),  # label styling (uppercase, secondary color) handled by theme.css
        dcc.Dropdown(
            id="ticker-dropdown",
            options=[{"label": t, "value": t} for t in TICKERS],
            value="AAPL",          # default selection
            clearable=False,
            style={"width": "200px", "marginBottom": "20px"},  # width is component-specific — kept inline
        ),

        # dcc.Loading wraps all financials outputs — shows a spinner immediately
        # while the Snowflake query runs so the page never looks broken or blank
        dcc.Loading(
            id="loading-financials",
            type="circle",  # circle spinner — clean, unobtrusive visual cue
            children=[
                # ── Revenue & Net Income grouped bar chart ────────────────
                dcc.Graph(id="price-chart"),

                # ── Net Income standalone bar chart ───────────────────────
                dcc.Graph(id="volume-chart"),

                # ── Summary stats table ───────────────────────────────────
                html.Div(id="stats-table"),  # marginTop removed — .dash-table CSS handles spacing
            ]
        ),

        # ── Data Quality — Anomaly Detection ─────────────────────────────
        html.Hr(),  # visual separator between the financials section and anomaly section
        html.H2("Data Quality — Anomaly Detection"),  # color set globally in theme.css
        html.P(
            # one-sentence description of the model and where results are tracked
            "IsolationForest model scores each ticker's YoY growth; outliers flagged as anomalies and tracked in MLflow.",
            className="dash-subtitle",  # CSS class applies muted color
        ),
        # Pipeline Health panel — row counts + freshness for the three core Snowflake tables
        dcc.Loading(
            id="loading-health",
            type="circle",  # consistent spinner style with the rest of the dashboard
            children=[html.Div(id="health-table")],  # marginBottom removed — theme.css handles spacing
        ),
        html.Button(
            "Refresh Anomalies",
            id="anomaly-refresh-btn",  # id referenced by the update_anomalies callback in callbacks.py
            n_clicks=0,
            className="dash-btn",  # CSS class applies dark button styling and hover states
        ),
        # Invisible stores: anomaly-data-store caches the raw anomaly data; anomaly-sort-state tracks which column is sorted
        dcc.Store(id="anomaly-data-store"),
        dcc.Store(id="anomaly-sort-state", data={"column": None, "direction": "asc"}),
        # Loading wraps only the scatter; table body below updates separately without a spinner
        dcc.Loading(
            id="loading-anomalies",
            type="circle",  # consistent spinner style across both sections
            children=[dcc.Graph(id="anomaly-scatter")],  # scatter of YoY growth colored by anomaly flag
        ),
        # Non-technical tip: explains legend-sync, single-click sort, and the × clear button
        html.P(
            "Tip: click a company name in the chart legend to show or hide it — "
            "the table below updates automatically. "
            "Click any column header to sort; click the same header again to flip "
            "between A→Z and Z→A order. "
            "When sorted, an × button appears at the top-right of the table — click it to clear the sort.",
            className="dash-subtitle",
        ),
        # Static header + dynamic body — header IDs are stable so sort callbacks can reference them
        html.Table(
            id="anomaly-table",  # id needed so the render callback can toggle the sort-active CSS class
            className="dash-table",
            children=[
                html.Thead(html.Tr([
                    html.Th("", className="color-dot-header"),  # color dot column — no id/n_clicks, not sortable
                    html.Th(["Ticker",          html.Span("", id="anom-sort-ind-ticker",              className="sort-indicator")], id="anom-col-ticker",             n_clicks=0, className="sortable-header"),
                    html.Th(["Fiscal Year",     html.Span("", id="anom-sort-ind-fiscal_year",         className="sort-indicator")], id="anom-col-fiscal_year",        n_clicks=0, className="sortable-header"),
                    html.Th(["Revenue YoY%",    html.Span("", id="anom-sort-ind-revenue_yoy_pct",     className="sort-indicator")], id="anom-col-revenue_yoy_pct",   n_clicks=0, className="sortable-header"),
                    html.Th(["Net Income YoY%", html.Span("", id="anom-sort-ind-net_income_yoy_pct",  className="sort-indicator")], id="anom-col-net_income_yoy_pct",n_clicks=0, className="sortable-header"),
                    html.Th(["Anomaly?",        html.Span("", id="anom-sort-ind-is_anomaly",          className="sort-indicator")], id="anom-col-is_anomaly",         n_clicks=0, className="sortable-header"),
                    html.Th(["Score",           html.Span("", id="anom-sort-ind-anomaly_score",       className="sort-indicator")], id="anom-col-anomaly_score",      n_clicks=0, className="sortable-header"),
                    html.Th("×", id="anom-sort-clear-btn", n_clicks=0, className="sort-clear-btn"),  # × button: hidden until a sort is active; clicking it clears the sort
                ])),
                html.Tbody(id="anomaly-table-body"),  # rows rendered by render_anomaly_table callback
            ]
        ),
    ]
)

register_routes(app)
register_callbacks(dash_app)
register_spot_callbacks(dash_app, "stocks")  # wire spot interruption callbacks onto the stocks Dash app
register_offline_callbacks(dash_app, "stocks")  # wire offline detection banner onto the stocks Dash app

# ── Weather Dashboard — second Dash app mounted on the same Flask server ──────
# Dash supports multiple Dash instances on one Flask app; each gets its own URL prefix
# and its own callback namespace, so there are no conflicts with the stocks callbacks.
weather_dash_app = dash.Dash(
    __name__,
    server=app,                    # share the same Flask instance to avoid spinning up a second server
    url_base_pathname="/weather/", # weather page lives at /weather/, stocks stays at /dashboard/
)

weather_dash_app.layout = html.Div(
    className="dash-page",  # CSS class handles max-width, centering, and padding
    children=[
        # Spot interruption banner: invisible intervals + fixed-position toast; no-op on non-EC2
        *build_spot_layout_components("weather"),
        # Offline detection banner: JS health check every 15s — shows if the server goes down
        *build_offline_layout_components("weather"),

        html.H1("Weather Analytics Pipeline"),  # color set globally in theme.css
        html.P(
            "Open-Meteo hourly forecast data for the top 10 US cities, ingested via Airflow → Kafka → Snowflake.",
            className="dash-subtitle",  # CSS class applies muted color and bottom margin
        ),

        # Navigation link back to the stocks dashboard — same nav pattern as stocks page
        html.Div(className="dash-nav", children=[
            html.A(
                "← View Stocks Dashboard",
                href="/dashboard/",  # points back to the stocks Dash app
                className="dash-nav__link",  # CSS class styles this as a pill-button link
            ),
        ]),

        # City selector — lets users switch between the 10 pre-loaded US cities without extra Snowflake queries
        html.Label("Select City:"),
        dcc.Dropdown(
            id="city-dropdown",
            options=[{"label": c, "value": c} for c in sorted(ALLOWED_CITIES)],
            value="New York",   # default city shown on page load
            clearable=False,
            style={"width": "250px", "marginBottom": "20px"},
        ),

        # Refresh button triggers the weather callback to reload data from Snowflake
        html.Button(
            "Refresh Weather",
            id="weather-refresh-btn",  # id referenced by update_weather callback in callbacks.py
            n_clicks=0,
            className="dash-btn",  # CSS class applies dark button styling and hover states
        ),

        # dcc.Loading wraps both weather outputs — shows a spinner while Snowflake is queried
        dcc.Loading(
            id="loading-weather",
            type="circle",  # consistent spinner style with the stocks dashboard
            children=[
                dcc.Graph(id="weather-temp-chart"),  # populated by update_weather callback — 7-day temperature line chart
                html.Div(id="weather-stats-table"),  # populated by update_weather callback — current temp + 24h stats
            ],
        ),

        # ── Data Quality — Anomaly Detection ─────────────────────────────────────
        html.Hr(),  # visual separator between charts and anomaly section
        html.H2("Data Quality — Anomaly Detection"),  # mirrors the stocks page section heading
        html.P(
            "Flags hourly temperature readings that are unusually high or low compared to each city's 7-day average.",
            className="dash-subtitle",
        ),
        # Pipeline Health panel — row count and freshness for the weather Snowflake table
        dcc.Loading(
            id="loading-weather-health",
            type="circle",
            children=[html.Div(id="weather-health-table")],  # populated by update_weather_health callback
        ),
        html.Button(
            "Refresh Anomalies",
            id="weather-anomaly-refresh-btn",  # triggers update_weather_anomalies callback in callbacks.py
            n_clicks=0,
            className="dash-btn",
        ),
        # Plain-language explanation of the anomaly method — sits right above the chart and table
        html.P(
            "How it works: for each city, the system calculates the average temperature "
            "over the past 7 days and how much readings typically vary. Any hourly reading "
            "that is far enough above or below that city's average is flagged as unusual. "
            "Each city is judged against its own recent pattern, so a hot reading in Phoenix "
            "is held to a different standard than the same temperature in Seattle. "
            "The chart below shows all readings over time — circles are normal, "
            "× marks are flagged as unusual for that city. "
            "The table lists only the flagged readings, sorted from most to least unusual.",
            className="dash-subtitle",  # muted color and spacing consistent with other descriptions
        ),
        # Invisible stores: weather-anomaly-data-store caches computed anomaly rows; sort state tracks active column
        dcc.Store(id="weather-anomaly-data-store"),
        dcc.Store(id="weather-anomaly-sort-state", data={"column": None, "direction": "asc"}),
        # Loading wraps only the scatter; table body below updates separately without a spinner
        dcc.Loading(
            id="loading-weather-anomalies",
            type="circle",
            children=[dcc.Graph(id="weather-anomaly-scatter")],  # scatter: temperature over time, color=city, shape=anomaly
        ),
        # Non-technical tip: explains legend-sync, single-click sort, and the × clear button
        html.P(
            "Tip: click a city name in the chart legend to show or hide it — "
            "the table below updates automatically. "
            "Click any column header to sort; click the same header again to flip "
            "between A→Z and Z→A order. "
            "When sorted, an × button appears at the top-right of the table — click it to clear the sort.",
            className="dash-subtitle",
        ),
        # Static header + dynamic body — header IDs are stable so sort callbacks can reference them
        html.Table(
            id="weather-anomaly-table",  # id needed so the render callback can toggle the sort-active CSS class
            className="dash-table",
            children=[
                html.Thead(html.Tr([
                    html.Th("", className="color-dot-header"),  # color dot column — no id/n_clicks, not sortable
                    html.Th(["City",               html.Span("", id="wanom-sort-ind-city_name",         className="sort-indicator")], id="wanom-col-city_name",        n_clicks=0, className="sortable-header"),
                    html.Th(["Date / Time",        html.Span("", id="wanom-sort-ind-observation_time",  className="sort-indicator")], id="wanom-col-observation_time", n_clicks=0, className="sortable-header"),
                    html.Th(["Temp (°F)",          html.Span("", id="wanom-sort-ind-temperature_f",     className="sort-indicator")], id="wanom-col-temperature_f",    n_clicks=0, className="sortable-header"),
                    html.Th(["City Avg (°F)",      html.Span("", id="wanom-sort-ind-city_mean",         className="sort-indicator")], id="wanom-col-city_mean",        n_clicks=0, className="sortable-header"),
                    html.Th(["Diff from Avg (°F)", html.Span("", id="wanom-sort-ind-deviation",         className="sort-indicator")], id="wanom-col-deviation",        n_clicks=0, className="sortable-header"),
                    html.Th(["Severity",           html.Span("", id="wanom-sort-ind-z_score",           className="sort-indicator")], id="wanom-col-z_score",          n_clicks=0, className="sortable-header"),
                    html.Th("×", id="wanom-sort-clear-btn", n_clicks=0, className="sort-clear-btn"),  # × button: hidden until a sort is active; clicking it clears the sort
                ])),
                html.Tbody(id="weather-anomaly-table-body"),  # rows rendered by render_weather_anomaly_table callback
            ]
        ),
    ],
)

register_weather_callbacks(weather_dash_app)  # wire the weather callbacks onto the weather Dash app
register_spot_callbacks(weather_dash_app, "weather")  # wire spot interruption callbacks onto the weather Dash app
register_offline_callbacks(weather_dash_app, "weather")  # wire offline detection banner onto the weather Dash app
# ─────────────────────────────────────────────────────────────────────────────

# Pre-warm the cache synchronously before Gunicorn forks workers (requires --preload in Dockerfile).
# Running this here means every forked worker inherits a hot _QUERY_CACHE via copy-on-write,
# so the very first user callback hits the in-memory cache instead of waiting on Snowflake.
prewarm_cache(TICKERS)


# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
