import dash
import flask
import os
import threading  # used to run cache pre-warming without blocking app startup
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
from security import init_security  # centralised security — rate limiting, headers, CORS
from spot import build_spot_layout_components, register_spot_callbacks  # spot interruption banner — safe no-op on non-EC2

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "")  # required for Flask sessions/cookies; loaded from K8s secret
if not app.config["SECRET_KEY"]:
    raise RuntimeError("FLASK_SECRET_KEY env var is not set — refusing to start without a secret key")  # fail fast rather than silently run insecure

init_security(app)  # attach rate limiting, security headers, and CORS — must run before routes are registered

# Dash mounted on the Flask server at /dashboard/
dash_app = dash.Dash(
    __name__,
    server=app,           # attach Dash to our existing Flask instance
    url_base_pathname="/dashboard/",
)

TICKERS = ["AAPL", "MSFT", "GOOGL"]  # must match the tickers loaded by the Airflow DAG

dash_app.layout = html.Div(
    className="dash-page",  # CSS class handles max-width, centering, and padding
    children=[
        # Spot interruption banner: invisible intervals + fixed-position toast; no-op on non-EC2
        *build_spot_layout_components("stocks"),

        html.H1("Stock Market Analytics Pipeline"),  # color set globally in theme.css
        html.P(
            "SEC EDGAR financial data pulled daily by Airflow → stored in MariaDB (→ Snowflake in Step 2).",
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
        # dcc.Loading wraps anomaly outputs — same pattern as financials section above
        dcc.Loading(
            id="loading-anomalies",
            type="circle",  # consistent spinner style across both sections
            children=[
                dcc.Graph(id="anomaly-scatter"),  # populated by update_anomalies callback — scatter of YoY growth colored by anomaly flag
                html.Div(id="anomaly-table"),  # populated by update_anomalies callback — detail table
            ]
        ),
    ]
)

register_routes(app)
register_callbacks(dash_app)
register_spot_callbacks(dash_app, "stocks")  # wire spot interruption callbacks onto the stocks Dash app

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

        html.H1("Weather Analytics Pipeline"),  # color set globally in theme.css
        html.P(
            "Open-Meteo hourly forecast data (lat=40°N, lon=40°E) ingested via Airflow → Kafka → Snowflake.",
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
    ],
)

register_weather_callbacks(weather_dash_app)  # wire the weather callbacks onto the weather Dash app
register_spot_callbacks(weather_dash_app, "weather")  # wire spot interruption callbacks onto the weather Dash app
# ─────────────────────────────────────────────────────────────────────────────

# Pre-warm the cache in a background thread immediately after startup — Snowflake is queried
# once here so every subsequent user request hits the in-memory cache instead of the DB.
# daemon=True means this thread won't block the process from shutting down if it's still running.
threading.Thread(target=lambda: prewarm_cache(TICKERS), daemon=True).start()


# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
