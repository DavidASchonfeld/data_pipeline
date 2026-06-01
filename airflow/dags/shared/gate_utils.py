"""Daily batch gate utilities — prevent duplicate Snowflake writes within the same calendar day."""

from datetime import date
from file_logger import OutputTextWriter  # used for task-level logging in gate functions


def _has_new_rows(row_count: int) -> bool:
    """Return True only if rows were actually written — gates dbt to avoid unnecessary runs."""
    return row_count > 0


def _is_genai_enabled() -> bool:
    """Return True only when the GenAI layer is on — gates the optional weather-summary task.

    Read at task runtime (not parse time): the value is correct in-pod because GENAI_ENABLED is carried
    in the genai-credentials secret, which is only applied when the layer is enabled (see sync.sh).
    """
    from shared.config import GENAI_ENABLED  # deferred — avoid coupling parse-time import order

    return GENAI_ENABLED


def check_daily_gate(variable_key: str, writer: OutputTextWriter) -> int:
    """Return 0 (skip) if already processed today, 1 (proceed) to continue.

    Compares today's ISO date against the Airflow Variable at variable_key.
    Returns an int because ShortCircuitOperator treats 0 as falsy (skip downstream tasks).
    """
    from airflow.sdk import Variable  # deferred — avoid parse-time import of Airflow internals

    today_iso = date.today().isoformat()  # e.g. "2026-04-12"
    last_write = Variable.get(variable_key, default="")  # empty string = first run; Airflow 3.x SDK raises AirflowRuntimeError on missing var (not KeyError)

    if last_write == today_iso:
        writer.log(f"Daily batch gate: already processed today ({today_iso}). Skipping.")  # suppress duplicate write
        return 0  # falsy — ShortCircuitOperator will skip downstream tasks

    writer.log(f"Daily batch gate: last write was '{last_write}', today is {today_iso} — proceeding.")
    return 1  # truthy — ShortCircuitOperator will allow downstream tasks
