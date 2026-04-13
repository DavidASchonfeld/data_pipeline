"""Shared DAG utilities — logging helpers used across all four DAGs and alerting callbacks."""

import pandas as pd
from file_logger import OutputTextWriter  # PVC log writer — same dependency used by all DAGs
from shared.config import LOCAL_LOG_PATH   # fallback path for local dev (not the K8s PVC path)


def get_writer() -> OutputTextWriter:
    """Return an OutputTextWriter pointed at /opt/airflow/out (K8s PVC), falling back to LOCAL_LOG_PATH."""
    try:
        return OutputTextWriter("/opt/airflow/out")  # K8s pod path — writable when PVC is mounted
    except PermissionError:
        return OutputTextWriter(LOCAL_LOG_PATH)       # fallback for local dev or non-PVC environments


def log_df_preview(writer: OutputTextWriter, df: pd.DataFrame) -> None:
    """Log the first 5 rows and column dtypes of df to the task writer."""
    writer.log(str(df.head()))    # first 5 rows — confirms shape and values
    writer.log(str(df.dtypes))    # column types — catches unexpected type coercions early
