import os

from dotenv import load_dotenv  # reads .env for local dev; no-op in production

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────
# Credentials come from environment variables — this file never contains secrets.
# Local dev:   set values in a .env file at the repo root (gitignored)
# Production:  set values in a Kubernetes Secret (see k8s-db-secret.yaml template)
DB_USER     = os.environ.get("DB_USER",     "airflow_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME     = os.environ.get("DB_NAME",     "database_one")
DB_HOST     = os.environ.get("DB_HOST",     "localhost")

# ── Alerting ──────────────────────────────────────────────────────────────────
# Slack webhook URL — empty string = log-only mode (no Slack messages sent)
# Local dev:   set in .env file at the repo root
# Production:  add to Kubernetes Secret alongside DB_USER/DB_PASSWORD/etc.
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Hours before data is considered stale (triggers alert)
# SEC EDGAR filings are weekly, so 168h (7 days) is a reasonable default
STALENESS_THRESHOLD_HOURS_STOCKS = int(os.environ.get("STALENESS_THRESHOLD_HOURS_STOCKS", "168"))
# Open-Meteo updates hourly and we pull every 5 min, so 2h means something is broken
STALENESS_THRESHOLD_HOURS_WEATHER = int(os.environ.get("STALENESS_THRESHOLD_HOURS_WEATHER", "2"))

# Minutes before a repeat alert is allowed for the same DAG+task or stale table (prevents spam)
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "60"))

# ── Local paths ───────────────────────────────────────────────────────────────
# Used as the fallback default path in OutputTextWriter for local dev.
# Production always passes /opt/airflow/out explicitly, so this default is never used there.
# Local dev:   set LOCAL_LOG_PATH in your .env file to your local logs directory
LOCAL_LOG_PATH = os.environ.get("LOCAL_LOG_PATH", "/tmp/airflow_logs")

# ── Weather ───────────────────────────────────────────────────────────────────
# City names must match ALLOWED_CITIES in dashboard/security.py
# Top 10 US cities by population — all fetched each DAG run so the dashboard never queries Snowflake per city click
WEATHER_CITIES = {
    "New York":     (40.7128, -74.0060),
    "Los Angeles":  (34.0522, -118.2437),
    "Chicago":      (41.8781, -87.6298),
    "Houston":      (29.7604, -95.3698),
    "Phoenix":      (33.4484, -112.0740),
    "Philadelphia": (39.9526, -75.1652),
    "San Antonio":  (29.4241, -98.4936),
    "San Diego":    (32.7157, -117.1611),
    "Dallas":       (32.7767, -96.7970),
    "Austin":       (30.2672, -97.7431),
}

# ── Anomaly detection ─────────────────────────────────────────────────────────
# IsolationForest hyperparameters — tune via env vars without editing DAG files or triggering re-parse
ANOMALY_CONTAMINATION = float(os.getenv("ANOMALY_CONTAMINATION", "0.05"))  # expected fraction of anomalies
ANOMALY_N_ESTIMATORS  = int(os.getenv("ANOMALY_N_ESTIMATORS",   "100"))   # number of isolation trees

# ── Kafka ─────────────────────────────────────────────────────────────────────
# Topic names and consumer-group IDs — renaming a topic only requires one change here
KAFKA_STOCKS_TOPIC  = "stocks-financials-raw"   # topic written by dag_stocks.py, read by dag_stocks_consumer.py
KAFKA_WEATHER_TOPIC = "weather-hourly-raw"       # topic written by dag_weather.py, read by dag_weather_consumer.py
KAFKA_STOCKS_GROUP  = "stocks-consumer-group"    # consumer group for stocks pipeline (offsets tracked per group)
KAFKA_WEATHER_GROUP = "weather-consumer-group"   # consumer group for weather pipeline

# ── MLflow ────────────────────────────────────────────────────────────────────
# Default points at the in-cluster K8s service; override with env var to change target
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.airflow-my-namespace.svc.cluster.local:5500")
