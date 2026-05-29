# SEC structured-extraction DAG (GenAI EPIC 4).
#
# Once a year, for each tracked ticker, this DAG runs the extraction runner as a subprocess under
# /opt/ml-venv: it fetches the company's 10-K, sends Risk Factors + MD&A through the LLM, and writes
# structured rows to PIPELINE_DB.ANALYTICS.FCT_FILING_EXTRACTS. Created PAUSED — unpause manually
# after a smoke test.
#
# GATING: this whole folder (genai_dags/) is only synced to the cluster when GENAI_ENABLED=true
# (scripts/deploy/sync.sh excludes it otherwise), so the DAG never exists on the pod when the AI
# layer is off — that deploy-time exclusion is the off-switch, no runtime flag check needed.
#
# Heavy imports (the LLM SDK, snowflake, bs4) live inside the runner subprocess, NOT in this file —
# DAG parsing in the scheduler stays light (only stdlib + the Airflow SDK at module top).

import json
import logging
import subprocess
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts (shared)

logger = logging.getLogger(__name__)

# Tracked tickers — mirrors TICKERS in dag_stocks.py (the companies this pipeline follows).
TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL"]

# Default fiscal year to extract: last completed calendar year (10-Ks lag the period they cover).
# Override per run via the trigger config — e.g. trigger with {"year": 2023} for the smoke test.
DEFAULT_FISCAL_YEAR: int = pendulum.now("America/New_York").year - 1

# Path to the ml-venv Python and the runner module (genai/ is baked into the image at /opt/airflow).
_ML_PYTHON = "/opt/ml-venv/bin/python"
_RUNNER_MODULE = "genai.runners.extract_runner"
_AIRFLOW_HOME = "/opt/airflow"  # cwd so `python -m genai...` can import the genai package


@dag(  # type: ignore
    "sec_filing_extract",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=15),  # generous ceiling per ticker (fetch + LLM calls)
        "on_failure_callback": on_failure_alert,
        "on_success_callback": on_success_alert,
        "on_retry_callback": on_retry_alert,
    },
    description="Extract structured facts (risk factors, revenue guidance) from each company's 10-K into ANALYTICS.FCT_FILING_EXTRACTS",
    schedule="@yearly",
    start_date=pendulum.datetime(2025, 1, 1, tz="America/New_York"),
    catchup=False,
    is_paused_upon_creation=True,   # never auto-runs on deploy — unpause manually after a smoke test
    max_active_tasks=1,             # serialize tickers: respects SEC's 10 req/s limit + caps peak RAM
    params={"year": DEFAULT_FISCAL_YEAR},
    tags=["genai", "stocks", "filings", "llm"],
)
def sec_filing_extract():
    """### SEC Filing Extraction Pipeline (GenAI)

    One mapped task per ticker. Each runs `genai.runners.extract_runner` as a subprocess and parses
    the JSON summary it prints on its last stdout line.
    """

    @task()
    def extract_filing(ticker: str, **context) -> dict:
        # Resolve the fiscal year from the run's params (overridable via trigger config).
        year = int(context["params"]["year"])
        logger.info("Extracting %s FY%s via %s", ticker, year, _RUNNER_MODULE)

        # Run the heavy work in the ml-venv subprocess — keeps the SDK out of the scheduler process.
        result = subprocess.run(
            [_ML_PYTHON, "-m", _RUNNER_MODULE, "--ticker", ticker, "--year", str(year)],
            capture_output=True,
            text=True,
            timeout=600,   # hard cap matching the roadmap; fetch + up to ~4 LLM calls fit comfortably
            cwd=_AIRFLOW_HOME,
        )

        # Surface the runner's logs in the Airflow task log for debugging.
        for line in result.stdout.splitlines():
            logger.info("[runner] %s", line)
        if result.returncode != 0:
            logger.error("[runner stderr] %s", result.stderr)
            raise RuntimeError(f"extract_runner failed for {ticker} FY{year} (rc={result.returncode})")

        # The last stdout line is the JSON summary; parse it as this task's return value.
        last_line = result.stdout.strip().splitlines()[-1]
        summary = json.loads(last_line)
        logger.info("%s FY%s: wrote %s rows, %s errors", ticker, year, summary.get("rows_written"), len(summary.get("errors", [])))
        return summary

    # One task instance per ticker (dynamic mapping); max_active_tasks=1 runs them one at a time.
    extract_filing.expand(ticker=TICKERS)


dag = sec_filing_extract()
