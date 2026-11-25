# EDGAR documents DAG: lands new SEC 10-K filings in the Bronze layer of the lake (S3 on the live box).
#
# Weekly, it runs the agent_data Bronze runner as a subprocess under /opt/ml-venv. The runner asks the SEC which
# 10-K / 10-K/A filings exist for the tracked companies, skips everything already in bronze/ingestion_log,
# stores each new filing's raw HTML plus a JSON sidecar (with its SHA-256), and verifies Bronze is consistent.
# A rerun downloads nothing new. Diagram: docs/agent_data/EDGAR_DOCUMENTS_DAG.md.
#
# GATING: this folder (agent_data_dags/) is only synced to the cluster when GENAI_ENABLED=true (see
# scripts/deploy/sync.sh), the same off-switch genai_dags/ uses. Created PAUSED — unpause after a smoke test.
#
# Heavy imports (delta-rs, requests, pyarrow) live in the runner subprocess, NOT here, so DAG parsing stays light.

import json
import logging
import subprocess
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts (shared)

logger = logging.getLogger(__name__)

# The ml-venv Python and the runner module (agent_data is pip-installed into ml-venv in the Airflow image).
_ML_PYTHON = "/opt/ml-venv/bin/python"
_RUNNER_MODULE = "agent_data.domains.edgar.run_bronze"
_AIRFLOW_HOME = "/opt/airflow"
# Hard cap for one run: the first full load is ~80 filings at <=8 requests/s; reruns take about a minute.
_RUNNER_TIMEOUT_SECONDS = 50 * 60


def _runner_command(dry_run: bool) -> list[str]:
    # Build the subprocess command; dry-run lists what would be fetched without downloading or writing.
    command = [_ML_PYTHON, "-m", _RUNNER_MODULE]
    return command + ["--dry-run"] if dry_run else command


def _parse_summary(stdout: str) -> dict:
    # The runner's last stdout line is one JSON summary; anything else means it died before reporting.
    lines = stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Bronze runner printed nothing; check the task log for a crash")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bronze runner's last line was not JSON: {lines[-1][:200]!r}") from exc


@dag(  # type: ignore
    "edgar_documents",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=55),
        "on_failure_callback": on_failure_alert,
        "on_success_callback": on_success_alert,
        "on_retry_callback": on_retry_alert,
    },
    description="Land new SEC 10-K filings (raw HTML + SHA-256 sidecar) in the Bronze lake layer",
    schedule="0 5 * * 0",           # Sundays 05:00 ET: off-peak for SEC servers; 10-Ks only arrive yearly
    start_date=pendulum.datetime(2026, 11, 1, tz="America/New_York"),
    catchup=False,
    is_paused_upon_creation=True,   # never auto-runs on deploy — unpause manually after a smoke test
    max_active_runs=1,              # one writer at a time keeps lake commits simple and SEC traffic polite
    params={"dry_run": False},      # trigger with {"dry_run": true} to preview without downloading
    tags=["agent_data", "edgar", "bronze", "lake"],
)
def edgar_documents():
    """### EDGAR documents → Bronze

    One task: runs `agent_data.domains.edgar.run_bronze` in ml-venv and returns its JSON summary
    (discovered / landed / skipped / failures plus a Bronze consistency report).
    """

    @task()
    def ingest_bronze(**context) -> dict:
        # Run the runner in ml-venv and surface its logs; a non-zero exit (failures or a bad verify) fails the task.
        dry_run = bool(context["params"].get("dry_run", False))
        result = subprocess.run(_runner_command(dry_run), capture_output=True, text=True,
                                timeout=_RUNNER_TIMEOUT_SECONDS, cwd=_AIRFLOW_HOME)
        for line in result.stderr.splitlines():
            logger.info("[runner] %s", line)
        summary = _parse_summary(result.stdout)
        logger.info("Bronze summary: %s", json.dumps(summary, sort_keys=True)[:4000])
        if result.returncode != 0:
            raise RuntimeError(f"Bronze runner failed (rc={result.returncode}): "
                               f"{summary.get('error') or summary.get('failures') or summary.get('verify')}")
        return summary

    ingest_bronze()


dag = edgar_documents()
