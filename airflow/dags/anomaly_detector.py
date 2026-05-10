# Standalone anomaly detection script - runs under /opt/ml-venv (scikit-learn + mlflow available)
# Reads FCT_COMPANY_FINANCIALS, fits IsolationForest on YoY pct changes, writes to FCT_ANOMALIES
# Called by: dag_stocks_consumer.py (detect_anomalies task via BashOperator -> /opt/ml-venv/bin/python)
# Scope: FINANCIAL anomalies only (revenue + net income YoY). Weather anomalies are not currently
#        detected anywhere in this pipeline — no equivalent script exists for FCT_WEATHER_HOURLY.
# NOTE: shared/ is not imported here because this script runs under a separate venv that may not
#       have the same sys.path as the Airflow workers. Constants are duplicated intentionally.
#
# ── What scikit-learn does ────────────────────────────────────────────────────
# scikit-learn is a Python library of ready-made machine learning algorithms. Think of it as a
# toolbox: instead of writing the math yourself, you pick a tool (here: IsolationForest), hand
# it your data, and it does the learning. In this script, scikit-learn's IsolationForest reads
# each company's year-over-year revenue and net income changes, builds a statistical picture of
# what "normal" looks like across all companies, and then scores every company on how far it
# deviates from that normal picture. Companies that deviate far enough get flagged as anomalies.
# scikit-learn's job ends there — it produces the flags and scores, but it doesn't remember
# anything. Run it again tomorrow and it starts completely from scratch.
#
# ── What MLflow does ─────────────────────────────────────────────────────────
# MLflow is the memory layer that scikit-learn lacks. Every time this script runs, MLflow
# opens a new "run" — a timestamped record that captures: (1) the settings the model used
# (e.g. "expect 5% of companies to be anomalies"), (2) what came out (how many were flagged),
# and (3) the trained model file itself, saved as a downloadable artifact. MLflow stores all
# of this in a searchable UI so you can browse every past run side by side and ask questions
# like "did changing the contamination setting last week change how many companies were flagged?"
# Think of MLflow as the logbook that sits next to the machine: scikit-learn runs the machine,
# MLflow writes down everything that happened each time it ran.
#
# ── How they work together ───────────────────────────────────────────────────
# The handoff is simple: scikit-learn trains the model and produces results → MLflow records
# that training session and saves the model → the results (with the MLflow run ID attached)
# are written to Snowflake. That run ID is the thread connecting every flagged company in
# Snowflake back to the exact MLflow record — settings, metrics, and saved model — that
# produced it. scikit-learn is the engine; MLflow is the flight recorder.

import os
import json
import argparse

# ── Snowflake table identifiers (mirrors shared/snowflake_schema.py) ──────────
# Defined locally because this script runs under /opt/ml-venv, not the Airflow venv.
# In Snowflake, a "schema" is a namespace inside a database that groups related tables together.
# Structure: DATABASE -> SCHEMA -> TABLE (e.g. PIPELINE_DB -> ANALYTICS -> FCT_ANOMALIES).
# Our database PIPELINE_DB has four schemas: RAW (raw ingest), STAGING (dbt views),
# MARTS (dbt fact/dim tables), and ANALYTICS (ML output like FCT_ANOMALIES).
_DB              = "PIPELINE_DB"
_MARTS_FCT_FIN   = f"{_DB}.MARTS.FCT_COMPANY_FINANCIALS"   # source table for feature engineering
_ANALYTICS_SCHEMA = f"{_DB}.ANALYTICS"                     # schema for DDL statements
_FCT_ANOMALIES   = f"{_DB}.ANALYTICS.FCT_ANOMALIES"        # output table for model results

import pandas as pd
import snowflake.connector                        # direct connector — no Airflow dependency
from sklearn.ensemble import IsolationForest     # ML model for unsupervised anomaly detection
import mlflow                                    # experiment tracking + model artifact logging
import mlflow.sklearn
from mlflow.models import infer_signature          # builds explicit input+output schema to silence int-column warning
from cryptography.hazmat.primitives import serialization   # used to load the RSA private key file at login time
from cryptography.hazmat.backends import default_backend


# ── Snowflake connection ─────────────────────────────────────────────────────

def _load_private_key_der() -> bytes:
    """Read the RSA private key file and return it as DER bytes — the format snowflake-connector wants."""
    # The path is set in the K8s secret; the file itself is mounted read-only into the pod
    key_path = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as f:
        # load_pem_private_key parses the PEM-encoded text on disk into a private key object
        p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    # Convert that object to DER bytes — the binary form Snowflake expects on the wire
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_snowflake_conn():
    """Open a Snowflake connection using env vars — avoids Airflow hook dependency."""
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=_load_private_key_der(),         # RSA key-pair auth — replaces password (no MFA prompt for service account)
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),  # explicit role — prevents default-role from silently locking created objects
    )


# ── Data fetching & feature engineering ─────────────────────────────────────
# "Feature engineering" means transforming raw data into numeric inputs the ML model can learn from.
# Raw values like "$5B revenue" are hard to compare across different-sized companies. Instead,
# we compute "how much did revenue CHANGE year-over-year?" — a normalized signal that works across
# companies regardless of their absolute size. These computed columns (revenue_yoy_pct,
# net_income_yoy_pct) are the "features": the numeric inputs the IsolationForest model reads.

def fetch_data(conn) -> pd.DataFrame:
    """
    Pull FY Revenues + NetIncomeLoss from the mart, pivot to wide format,
    compute YoY % change per CIK, and drop the first year (NaN row).
    """
    query = f"""
        SELECT cik, ticker, fiscal_year, metric, value
        FROM {_MARTS_FCT_FIN}
        WHERE UPPER(metric) IN ('REVENUEFROMCONTRACTWITHCUSTOMEREXCLUDINGASSESSEDTAX', 'NETINCOMELOSS')  -- matches XBRL concept fetched by edgar_client.py
          AND fiscal_period = 'FY'
    """
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cols = [desc[0].lower() for desc in cur.description]   # lowercase column names for consistency
    df = pd.DataFrame(rows, columns=cols)

    df["metric"] = df["metric"].str.lower()  # normalize metric case before pivot to avoid rename mismatch

    # Pivot: one row per (cik, ticker, fiscal_year), columns = revenue, net_income
    wide = df.pivot_table(
        index=["cik", "ticker", "fiscal_year"],
        columns="metric",
        values="value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None                                 # drop the leftover 'metric' axis name
    wide = wide.rename(columns={
        "revenuefromcontractwithcustomerexcludingassessedtax": "revenue",  # XBRL name from edgar_client.py, lowercased
        "netincomeloss": "net_income",  # lowercased by str.lower() above — was "NetIncomeLoss"
    })

    # Sort so pct_change() computes correctly within each CIK group
    wide = wide.sort_values(["cik", "fiscal_year"]).reset_index(drop=True)

    # "pct_change" is pandas' method name: pct = percentage, so pct_change() = percentage change.
    # It computes (current_value - previous_value) / previous_value for each consecutive row.
    # Example: if revenue was $100 last year and $120 this year, pct_change = (120-100)/100 = 0.20 (20% growth).
    # We group by CIK (not ticker) because CIK is the SEC's permanent, never-changing company identifier.
    # Tickers can be renamed (e.g. Facebook -> META), so grouping by ticker would split one company's
    # history into two separate groups, breaking the YoY calculation at the rename boundary.
    # CIK stays constant forever regardless of rebrands or ticker changes.
    # The first year per CIK has no "previous year" to compare against, so it produces NaN and is dropped below.
    wide[["revenue_yoy_pct", "net_income_yoy_pct"]] = (
        wide.groupby("cik")[["revenue", "net_income"]].pct_change(fill_method=None)  # fill_method=None: explicit no-ffill, suppresses FutureWarning from deprecated default
    )

    # Drop first year per CIK (NaN YoY) — no baseline to compare against
    wide = wide.dropna(subset=["revenue_yoy_pct", "net_income_yoy_pct"]).reset_index(drop=True)

    return wide


# ── Model training + MLflow logging ─────────────────────────────────────────
# HOW ISOLATIONFOREST WORKS — trees, forests, and anomaly scores:
#
# A "decision tree" is a series of yes/no splits on your data. For example:
#   "Is revenue_yoy_pct > 0.5?" → yes → "Is net_income_yoy_pct > 0.3?" → yes → leaf node
# Each split partitions the data into smaller groups. A normal data point (like typical 10%
# revenue growth) lives in a dense cluster — it takes MANY splits to isolate it because many
# other points look similar. An anomalous point (like -80% revenue crash) is rare and unusual
# — it gets isolated in very FEW splits because almost nothing else looks like it.
#
# A "forest" is just many trees (n_estimators=100 means 100 trees), each built on a random
# subset of the data with random split points. The model averages the path length across all
# 100 trees: short average path = anomaly, long average path = normal point.
#
# This is "unsupervised" detection: we never tell the model which companies ARE anomalies.
# It figures out what's unusual purely from the shape of the data.

def run_model(df: pd.DataFrame, contamination: float, n_estimators: int) -> tuple[pd.DataFrame, str]:
    """
    Fit IsolationForest on YoY features, annotate df with results, log to MLflow.
    Returns (annotated_df, mlflow_run_id).
    """
    # Keep as DataFrame (not .values) so sklearn remembers feature names — prevents "fitted without feature names" warning
    features_df = df[["revenue_yoy_pct", "net_income_yoy_pct"]]

    # Fall back to in-cluster K8s service address if env var absent (matches shared/config.py default)
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow.airflow-my-namespace.svc.cluster.local:5500"))  # point at the MLflow server

    # An MLflow "experiment" is a named container that groups related runs together.
    # Every time this script runs, it creates a new "run" inside the "anomaly_detection" experiment.
    # You can then open the MLflow UI and compare run #1 vs run #2 vs run #3: did more companies
    # look anomalous this month? Did changing contamination from 0.05 to 0.10 matter?
    #
    # "Soft-deleted" means the experiment was deleted via the MLflow UI but is still in the
    # database (MLflow never hard-deletes by default). set_experiment() cannot reuse a deleted
    # experiment by name — it would crash. So we detect that state and restore it first.
    # We also check artifact_location to avoid restoring stale experiments from old runs
    # that pointed at a local disk path instead of the HTTP artifact proxy.
    #
    # An "artifact" in MLflow is any file saved alongside a run: the trained model file,
    # a confusion matrix image, a feature importance CSV, etc. artifact_location is the
    # storage root URL where those files live. "mlflow-artifacts:/" means the MLflow server's
    # built-in artifact proxy (our setup), vs. a raw path like "/tmp/mlruns" which would be
    # a stale experiment from an old local-disk configuration.
    _client = mlflow.tracking.MlflowClient()
    _exp = _client.get_experiment_by_name("anomaly_detection")
    if _exp is not None and _exp.lifecycle_stage == "deleted":
        # Only restore if artifact root is the HTTP proxy — skip stale local-path experiments
        if _exp.artifact_location == "mlflow-artifacts:/":
            _client.restore_experiment(_exp.experiment_id)
    mlflow.set_experiment("anomaly_detection")                         # group all runs under one experiment

    with mlflow.start_run():
        model = IsolationForest(
            contamination=contamination,  # fraction of data expected to be anomalies (e.g. 0.05 = 5%)
            n_estimators=n_estimators,    # number of isolation trees in the forest
            random_state=42,              # reproducibility — same seed = same tree splits every run
        )
        model.fit(features_df)  # build all 100 trees from the training data

        # .predict() returns a label for every row: 1 = normal, -1 = anomaly.
        # The threshold is set by contamination: if contamination=0.05, the 5% of points
        # with the shortest average isolation path are labeled -1 (anomaly).
        # We convert to bool (True/False) for readability in the output table.
        df["is_anomaly"] = model.predict(features_df) == -1

        # .score_samples() returns the raw anomaly score for every row — a negative float.
        # More negative = more anomalous. Example: a score of -0.65 is more anomalous than -0.45.
        # This gives a continuous ranking, whereas predict() only gives a binary label.
        # The dashboard uses this to rank "how anomalous" a company is, not just yes/no.
        df["anomaly_score"] = model.score_samples(features_df)

        n_anomalies = int(df["is_anomaly"].sum())
        n_total = len(df)

        # Log hyperparams + metrics for experiment comparison
        mlflow.log_param("contamination", contamination)
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_metric("n_anomalies", n_anomalies)
        mlflow.log_metric("n_total", n_total)
        mlflow.log_metric("contamination_rate", n_anomalies / n_total if n_total else 0)

        # Cast input sample to float64 so MLflow infers float input schema (not int)
        input_ex = features_df.iloc[:5].astype("float64")

        # infer_signature() inspects the actual input DataFrame and output array and builds
        # a "signature" — a schema that describes exactly what the model expects as input and
        # what it will return as output (column names, data types). This gets saved alongside
        # the model artifact so that anyone loading the model later knows exactly what to feed it.
        # "sig" is just a short variable name for this signature object.
        # We cast predict() output to float here to avoid an MLflow warning about int64 columns.
        sig = infer_signature(input_ex, model.predict(features_df).astype(float))

        # log_model() saves the trained model as an artifact file inside this MLflow run.
        # "isolation_forest" is the subfolder name inside the run's artifact directory.
        # With the signature attached, MLflow can validate inputs at inference time.
        mlflow.sklearn.log_model(model, "isolation_forest", input_example=input_ex, signature=sig)

        run_id = mlflow.active_run().info.run_id

    # Stamp every result row with the ID of the MLflow run that produced it.
    # This creates a direct link from each Snowflake row back to the exact model run —
    # if a flagged company is ever questioned, grab its mlflow_run_id, open MLflow, and
    # you can see precisely which model settings and training data produced that flag.
    df["mlflow_run_id"] = run_id
    return df, run_id


# ── Snowflake write ──────────────────────────────────────────────────────────

def write_results(conn, df: pd.DataFrame) -> None:
    """
    Create ANALYTICS schema + FCT_ANOMALIES table if missing, then full-refresh via DELETE + INSERT.
    """
    cur = conn.cursor()

    # Create schema + table only if they don't already exist
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_ANALYTICS_SCHEMA}")  # schema name from local constant
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FCT_ANOMALIES} (
            cik             VARCHAR,
            ticker          VARCHAR,
            fiscal_year     NUMBER,
            revenue_yoy_pct FLOAT,
            net_income_yoy_pct FLOAT,
            is_anomaly      BOOLEAN,
            anomaly_score   FLOAT,
            detected_at     TIMESTAMP_NTZ,
            mlflow_run_id   VARCHAR
        )
    """)

    # Full-refresh: wipe previous run's results before inserting new ones
    cur.execute(f"DELETE FROM {_FCT_ANOMALIES}")  # table name from local constant

    # Build rows as tuples for executemany — CURRENT_TIMESTAMP() resolved server-side
    insert_sql = f"""
        INSERT INTO {_FCT_ANOMALIES}
            (cik, ticker, fiscal_year, revenue_yoy_pct, net_income_yoy_pct,
             is_anomaly, anomaly_score, detected_at, mlflow_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP(), %s)
    """
    rows = [
        (
            str(row["cik"]),
            row["ticker"],
            int(row["fiscal_year"]),
            float(row["revenue_yoy_pct"]),
            float(row["net_income_yoy_pct"]),
            bool(row["is_anomaly"]),
            float(row["anomaly_score"]),
            str(row["mlflow_run_id"]),
        )
        for _, row in df.iterrows()
    ]
    cur.executemany(insert_sql, rows)
    conn.commit()    # commit after insert to persist the transaction


# ── CLI + pipeline orchestration ─────────────────────────────────────────────
# THIS SECTION IS the actual entry point called by the DAG.
#
# When Airflow runs the pipeline, dag_stocks_consumer.py reaches a step called
# "detect_anomalies". That step doesn't import this file — it runs it as a
# shell command, the same way you'd type a command in a terminal:
#
#   /opt/ml-venv/bin/python anomaly_detector.py --contamination 0.05 --n-estimators 100
#
# The `if __name__ == "__main__"` block at the bottom is what executes when the
# script is launched that way. It reads the --contamination and --n-estimators
# flags passed by the DAG, runs the full pipeline (fetch → model → write), and
# prints a JSON summary to stdout. Airflow captures that last line as the task's
# return value.
#
# Why a shell command instead of a normal import? This script needs scikit-learn
# and mlflow, which live in a separate Python environment (/opt/ml-venv) from the
# one Airflow workers use. Running it as a shell command is the cleanest way to
# cross that boundary without mixing the two environments.
#
# run_pipeline() is kept as a separate function (not just code inside __main__)
# so tests can call it directly without simulating a command-line invocation.

def parse_args() -> argparse.Namespace:
    """Parse CLI args so Airflow can override contamination and n_estimators."""
    parser = argparse.ArgumentParser(description="IsolationForest anomaly detection for stock financials")
    parser.add_argument("--contamination", type=float, default=0.05,   # expected anomaly fraction
                        help="IsolationForest contamination (default 0.05)")
    parser.add_argument("--n-estimators", type=int, default=100,       # number of trees in the forest
                        help="IsolationForest n_estimators (default 100)")
    return parser.parse_args()


def run_pipeline(contamination: float, n_estimators: int) -> dict:
    """
    Full pipeline: connect → fetch → model → write → return summary dict.
    Isolated in a function so it can be unit-tested without __main__.
    """
    conn = get_snowflake_conn()
    try:
        df = fetch_data(conn)                                          # pull + engineer features
        df, run_id = run_model(df, contamination, n_estimators)       # fit model + log to MLflow
        write_results(conn, df)                                        # persist to Snowflake
    finally:
        conn.close()    # always release connection even on error

    return {
        "n_anomalies": int(df["is_anomaly"].sum()),
        "n_total": len(df),
        "mlflow_run_id": run_id,
    }


if __name__ == "__main__":
    args = parse_args()
    summary = run_pipeline(args.contamination, args.n_estimators)
    print(json.dumps(summary))    # last line of stdout — Airflow task parses this as the return value
