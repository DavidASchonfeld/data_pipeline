import concurrent.futures
import threading
import time

import pandas as pd
from sqlalchemy import create_engine, text

# All environment variables and constants come from config.py — this file never reads os.environ directly
from config import (
    SQL_USERNAME, SQL_PASSWORD, SQL_DATABASE, SQL_URL, DB_BACKEND,
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
    SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE,
    CACHE_TTL_FINANCIALS, CACHE_TTL_WEATHER,
)

# ── Database connection ───────────────────────────────────────────────────────
# Credentials come from config.py which reads environment variables — this file never contains secrets.
# Local dev:   set values in a .env file at the repo root (gitignored)
# Production:  set values in a Kubernetes Secret referenced by the Flask Deployment
# Step 2 swap: only the env var values change — this code stays identical for Snowflake
if DB_BACKEND == "snowflake":
    # Snowflake engine — set DB_BACKEND=snowflake in the K8s secret to activate
    from snowflake.sqlalchemy import URL as SnowflakeURL
    DB_ENGINE = create_engine(SnowflakeURL(
        account=SNOWFLAKE_ACCOUNT,
        user=SNOWFLAKE_USER,
        password=SNOWFLAKE_PASSWORD,
        database=SNOWFLAKE_DATABASE,
        schema=SNOWFLAKE_SCHEMA,  # dashboard reads MARTS, not RAW
        warehouse=SNOWFLAKE_WAREHOUSE,
        role=SNOWFLAKE_ROLE,  # explicit role — prevents default role from blocking MARTS table access
    ))
else:
    # MariaDB engine (default) — stays active until DB_BACKEND=snowflake is set
    try:
        DB_ENGINE = create_engine(
            f"mysql+pymysql://{SQL_USERNAME}:{SQL_PASSWORD}@{SQL_URL}/{SQL_DATABASE}"
        )
    except Exception:
        DB_ENGINE = None  # pymysql not installed locally — queries will return empty frames

# ── Snowflake table identifiers (centralized to avoid scattered hardcoded strings) ───
_TBL_FINANCIALS = "PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS"  # annual SEC EDGAR mart
_TBL_WEATHER    = "PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY"      # hourly weather mart
_TBL_ANOMALIES  = "PIPELINE_DB.ANALYTICS.FCT_ANOMALIES"       # IsolationForest results
# ─────────────────────────────────────────────────────────────────────────────

# ── Query cache (cost optimization #2) ───────────────────────────────────────
_QUERY_CACHE: dict = {}       # {key: (dataframe, expires_at)} — TTLs imported from config.py

# Set by prewarm_cache() when all startup queries finish; /health/ready blocks on this
_prewarm_event = threading.Event()

def _cache_get(key: str):
    """Return cached value if present and not expired, else None."""
    entry = _QUERY_CACHE.get(key)
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value, ttl: int) -> None:
    """Store value with a monotonic expiry timestamp."""
    _QUERY_CACHE[key] = (value, time.monotonic() + ttl)

def _cached_query(key: str, ttl: int, columns: list, query_fn) -> pd.DataFrame:
    """Run query_fn() and cache the result; return empty DataFrame when not on Snowflake.

    Centralizes the Snowflake guard + cache-check + cache-set pattern that was
    previously repeated verbatim in load_weather_data, load_anomalies, and load_pipeline_health.
    query_fn: zero-arg callable that queries Snowflake and returns a DataFrame.
    columns:  column list used to type the empty guard DataFrame.
    """
    if DB_BACKEND != "snowflake":
        return pd.DataFrame(columns=columns)  # guard: these tables only exist in Snowflake
    cached = _cache_get(key)
    if cached is not None:
        return cached  # cache hit — skip the Snowflake round-trip
    result = query_fn()  # execute the query now that cache missed
    _cache_set(key, result, ttl)
    return result
# ─────────────────────────────────────────────────────────────────────────────


def _load_ticker_data(ticker: str) -> pd.DataFrame:
    """Query MariaDB for annual Revenue and Net Income rows from company_financials.

    Private helper (leading underscore) because it's only called by the Dash
    callback — not part of the public API of this module.
    A new DB connection is opened per call; SQLAlchemy's connection pool
    handles reuse and cleanup automatically.
    Filters to fiscal_period='FY' to return one row per metric per annual filing.
    """
    # Return empty frame if no engine is available (e.g. pymysql not installed locally)
    if DB_ENGINE is None:
        return pd.DataFrame(columns=["metric", "label", "period_end", "value", "fiscal_year", "fiscal_period"])

    # Return cached result if still fresh
    cache_key = f"financials:{ticker}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # :ticker is a SQLAlchemy named bind parameter; its value is supplied by params={"ticker": ticker} below
    # Use fully-qualified Snowflake name or short MariaDB table name depending on backend
    table = _TBL_FINANCIALS if DB_BACKEND == "snowflake" else "company_financials"
    query = text(f"""
        SELECT metric, label, period_end, value, fiscal_year, fiscal_period
        FROM {table}
        WHERE ticker = :ticker
          AND metric IN ('Revenues', 'NetIncomeLoss')
          AND fiscal_period = 'FY'
        ORDER BY period_end ASC
    """)
    with DB_ENGINE.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    # Cast period_end to datetime so Plotly renders the x-axis correctly
    df["period_end"] = pd.to_datetime(df["period_end"])
    _cache_set(cache_key, df, CACHE_TTL_FINANCIALS)
    return df


# Stub: wire up when stock_daily_prices DAG is added in Step 2
def _load_ohlcv_data(ticker: str) -> pd.DataFrame:  # noqa: ARG001
    """Placeholder for OHLCV price query — not yet called.

    When a DAG that populates stock_daily_prices (OHLCV) is implemented in Step 2,
    wire this function into update_charts() to restore the candlestick chart.
    """
    raise NotImplementedError("stock_daily_prices DAG not yet implemented (Step 2)")


# ── Cache pre-warming ─────────────────────────────────────────────────────────
def prewarm_cache(tickers: list) -> None:
    """Query Snowflake for all tickers + anomalies at startup so the first user request hits the cache, not the DB.

    Called in a background thread from app.py immediately after the Flask container starts.
    Runs all queries in parallel to reduce wall time from ~3 min (sequential) to ~30–60s.
    Sets _prewarm_event when done so /health/ready can signal Lambda that data is ready.
    Failures are silenced — a cache miss on first request is acceptable; a crash at startup is not.
    """
    def _safe(fn, label, *args):
        # Wrapper that catches and logs errors without crashing the thread pool
        try:
            fn(*args)
        except Exception as e:
            print(f"[prewarm_cache] WARNING: failed to warm '{label}': {e}", flush=True)

    # Fire all Snowflake queries concurrently — one warehouse activation covers all
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs  = [pool.submit(_safe, _load_ticker_data, f"financials:{t}", t) for t in tickers]
        futs += [pool.submit(_safe, load_anomalies,       "anomalies")]
        futs += [pool.submit(_safe, load_weather_data,    "weather")]
        futs += [pool.submit(_safe, load_stock_health,   "stock_health")]    # warm stock-only health panel
        futs += [pool.submit(_safe, load_weather_health, "weather_health")]  # warm weather-only health panel
        concurrent.futures.wait(futs)  # block until every query finishes or fails

    _prewarm_event.set()  # unblock /health/ready — Lambda can now redirect safely
    print("[prewarm_cache] complete — cache is warm", flush=True)
# ─────────────────────────────────────────────────────────────────────────────

# ── Weather data ─────────────────────────────────────────────────────────────
# Column list defined once so the guard path and real query always return the same schema
WEATHER_COLUMNS = ["observation_time", "temperature_f", "latitude", "longitude", "elevation", "timezone", "city_name"]

def load_weather_data() -> pd.DataFrame:
    """Return last 7 days of hourly weather from FCT_WEATHER_HOURLY; empty DataFrame if unavailable.

    Only runs against Snowflake — FCT_WEATHER_HOURLY does not exist in MariaDB.
    Cached for 15 minutes (CACHE_TTL_WEATHER) because forecast data updates hourly.
    """
    def _query():
        # includes city_name for dashboard dropdown filtering — all cities fetched once, filtered client-side
        # _TBL_WEATHER is a hardcoded constant (not user input), so f-string is safe here
        # exclude 'Unknown' — legacy rows from before multi-city support
        query = text(f"""
            SELECT observation_time, temperature_f, latitude, longitude, elevation, timezone, city_name
            FROM {_TBL_WEATHER}
            WHERE observation_time >= DATEADD('day', -7, CURRENT_TIMESTAMP())
              AND city_name != 'Unknown'
            ORDER BY city_name, observation_time ASC
        """)
        try:
            with DB_ENGINE.connect() as conn:
                return pd.read_sql(query, conn)  # execute and load all rows into a DataFrame
        except Exception:
            # Table may not have data yet or Snowflake may be briefly unavailable — return empty frame
            return pd.DataFrame(columns=WEATHER_COLUMNS)

    return _cached_query("weather", CACHE_TTL_WEATHER, WEATHER_COLUMNS, _query)  # 15-min cache — matches Open-Meteo refresh cadence
# ─────────────────────────────────────────────────────────────────────────────

# ── Anomaly detection results ─────────────────────────────────────────────────
# Column list defined once so both the guard path and the real query always return
# a DataFrame with the same schema — prevents KeyError in downstream callers.
ANOMALY_COLUMNS = [
    "ticker", "fiscal_year", "revenue_yoy_pct", "net_income_yoy_pct",
    "is_anomaly", "anomaly_score", "detected_at", "mlflow_run_id",
]

def load_anomalies() -> pd.DataFrame:
    """Return anomaly detection scores from FCT_ANOMALIES; empty DataFrame if unavailable.

    Table is created by the first anomaly_detector DAG run, not at deploy time,
    so every code path that can't reach it returns a typed empty DataFrame.
    """
    def _query():
        # _TBL_ANOMALIES is a hardcoded constant (not user input), so f-string is safe here
        query = text(f"""
            SELECT ticker, fiscal_year, revenue_yoy_pct, net_income_yoy_pct,
                   is_anomaly, anomaly_score, detected_at, mlflow_run_id
            FROM {_TBL_ANOMALIES}
            ORDER BY is_anomaly DESC, anomaly_score ASC
        """)
        try:
            with DB_ENGINE.connect() as conn:
                return pd.read_sql(query, conn)  # execute query and load all rows into a DataFrame
        except Exception:
            # Table may not exist yet if the DAG hasn't run — return empty frame so the dashboard doesn't crash
            return pd.DataFrame(columns=ANOMALY_COLUMNS)

    return _cached_query("anomalies", CACHE_TTL_FINANCIALS, ANOMALY_COLUMNS, _query)  # 1-hour cache — matches financials TTL
# ─────────────────────────────────────────────────────────────────────────────

# ── Pipeline health (freshness + row counts) ──────────────────────────────────
# Column list defined once so the guard path and real query always return the same schema
HEALTH_COLUMNS = ["table_name", "row_count", "latest_ts"]

def load_pipeline_health() -> pd.DataFrame:
    """Return row counts and latest timestamps for the three core tables; empty DataFrame if unavailable.

    Single UNION ALL query — one warehouse activation, result cached 1 hour.
    """
    def _query():
        # Single UNION ALL covers all three tables in one warehouse activation
        # Table names are hardcoded constants (not user input), so f-string is safe here
        query = text(f"""
            SELECT 'Financials' AS table_name, COUNT(*) AS row_count,
                   MAX(filed_date)::TIMESTAMP_NTZ AS latest_ts
            FROM {_TBL_FINANCIALS}
            UNION ALL
            SELECT 'Weather', COUNT(*), MAX(imported_at)
            FROM {_TBL_WEATHER}
            UNION ALL
            SELECT 'Anomalies', COUNT(*), MAX(detected_at)
            FROM {_TBL_ANOMALIES}
        """)
        try:
            with DB_ENGINE.connect() as conn:
                return pd.read_sql(query, conn)  # execute and load all three rows into a DataFrame
        except Exception:
            # Table may not exist yet or Snowflake may be briefly unavailable — return empty frame
            return pd.DataFrame(columns=HEALTH_COLUMNS)

    return _cached_query("pipeline_health", CACHE_TTL_FINANCIALS, HEALTH_COLUMNS, _query)  # 1-hour cache — matches financials TTL


def load_stock_health() -> pd.DataFrame:
    """Return pipeline health for stock-related tables only (Financials + Anomalies).

    Separate from weather health so each dashboard page shows only its own tables.
    """
    def _query():
        # Single UNION query for stock-related tables — one warehouse activation
        query = text(f"""
            SELECT 'Financials' AS table_name, COUNT(*) AS row_count,
                   MAX(filed_date)::TIMESTAMP_NTZ AS latest_ts
            FROM {_TBL_FINANCIALS}
            UNION ALL
            SELECT 'Anomalies', COUNT(*), MAX(detected_at)
            FROM {_TBL_ANOMALIES}
        """)
        try:
            with DB_ENGINE.connect() as conn:
                return pd.read_sql(query, conn)
        except Exception:
            return pd.DataFrame(columns=HEALTH_COLUMNS)

    return _cached_query("stock_health", CACHE_TTL_FINANCIALS, HEALTH_COLUMNS, _query)  # 1-hour cache — matches financials TTL


def load_weather_health() -> pd.DataFrame:
    """Return pipeline health for weather table only.

    Separate from stock health so the weather page shows only its own table freshness.
    """
    def _query():
        # Single row query for the weather mart table
        query = text(f"""
            SELECT 'Weather' AS table_name, COUNT(*) AS row_count, MAX(imported_at) AS latest_ts
            FROM {_TBL_WEATHER}
        """)
        try:
            with DB_ENGINE.connect() as conn:
                return pd.read_sql(query, conn)
        except Exception:
            return pd.DataFrame(columns=HEALTH_COLUMNS)

    return _cached_query("weather_health", CACHE_TTL_WEATHER, HEALTH_COLUMNS, _query)  # 15-min cache — matches weather TTL
# ─────────────────────────────────────────────────────────────────────────────
