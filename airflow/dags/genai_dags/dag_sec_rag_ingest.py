# RAG ingest DAG (GenAI EPIC 7).
#
# Every week this DAG refreshes the pgvector search index: it runs the ingest runner as a subprocess
# under /opt/ml-venv once per source. The runner pulls rows from Snowflake (cleaned 10-K section text,
# weekly weather summaries), chunks them, embeds only what changed since last run, upserts into the
# pgvector `chunks` table, and deletes chunks whose source row is gone. Created PAUSED — unpause
# manually after a smoke test.
#
# GATING: this whole folder (genai_dags/) is only synced to the cluster when GENAI_ENABLED=true
# (scripts/deploy/sync.sh excludes it otherwise), so no runtime flag check is needed.
#
# Heavy imports (the embedding model, psycopg2, snowflake) live inside the runner subprocess, NOT in
# this file — DAG parsing in the scheduler stays light (only stdlib + the Airflow SDK at module top).

import json
import logging
import subprocess
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts (shared)

logger = logging.getLogger(__name__)

# Path to the ml-venv Python and the runner module (genai/ is baked into the image at /opt/airflow).
_ML_PYTHON = "/opt/ml-venv/bin/python"
_RUNNER_MODULE = "genai.runners.ingest_runner"
_AIRFLOW_HOME = "/opt/airflow"  # cwd so `python -m genai...` can import the genai package


def _run_ingest(source: str) -> dict:
    # Run the ingest runner for one source as a subprocess and parse its JSON summary (last stdout line).
    logger.info("Ingesting source=%s via %s", source, _RUNNER_MODULE)
    result = subprocess.run(
        [_ML_PYTHON, "-m", _RUNNER_MODULE, "--source", source],
        capture_output=True,
        text=True,
        timeout=600,   # hard cap; chunk + embed for a few tickers / cities fits comfortably
        cwd=_AIRFLOW_HOME,
    )

    # Surface the runner's logs in the Airflow task log for debugging.
    for line in result.stdout.splitlines():
        logger.info("[runner] %s", line)
    if result.returncode != 0:
        logger.error("[runner stderr] %s", result.stderr)
        raise RuntimeError(f"ingest_runner failed for source={source} (rc={result.returncode})")

    # The last stdout line is the JSON summary; parse it as this task's return value.
    last_line = result.stdout.strip().splitlines()[-1]
    summary = json.loads(last_line)
    logger.info("source=%s: upserted %s, skipped %s, deleted %s",
                source, summary.get("chunks_upserted"), summary.get("chunks_skipped"), summary.get("orphans_deleted"))
    return summary


@dag(  # type: ignore
    "sec_rag_ingest",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=15),  # generous ceiling per source (read + chunk + embed)
        "on_failure_callback": on_failure_alert,
        "on_success_callback": on_success_alert,
        "on_retry_callback": on_retry_alert,
    },
    description="Chunk + embed filing section text and weather summaries from Snowflake into the pgvector chunks table",
    schedule="@weekly",
    start_date=pendulum.datetime(2025, 1, 1, tz="America/New_York"),
    catchup=False,
    is_paused_upon_creation=True,   # never auto-runs on deploy — unpause manually after a smoke test
    max_active_tasks=1,             # serialize the two sources: caps peak RAM (one model load at a time)
    tags=["genai", "rag", "ingest"],
)
def sec_rag_ingest():
    """### RAG Ingest Pipeline (GenAI)

    Two tasks, one per source, each running `genai.runners.ingest_runner` as a subprocess and parsing
    the JSON summary it prints on its last stdout line.
    """

    @task()
    def ingest_filings() -> dict:
        return _run_ingest("filings")

    @task()
    def ingest_weather() -> dict:
        return _run_ingest("weather")

    # Independent sources; max_active_tasks=1 still runs them one at a time so only one model loads at once.
    ingest_filings()
    ingest_weather()


dag = sec_rag_ingest()
