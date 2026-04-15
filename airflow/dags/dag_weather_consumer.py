# General Libraries

import json
from typing import Any
from datetime import timedelta, date

import pendulum
from airflow.sdk import dag, task, XComArg, Variable  # Airflow 3.x SDK
from airflow.providers.standard.operators.python import ShortCircuitOperator  # skips dbt if no new rows written
from shared.dbt_utils import make_dbt_operator  # shared factory: eliminates copy-pasted BashOperator blocks


# My Files
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from shared.utils import get_writer, log_df_preview  # shared log writer factory and DataFrame preview helper
from shared.gate_utils import _has_new_rows  # shared gate: True if rows > 0 — avoids duplicating in both consumer DAGs
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts


@dag(  # type:ignore
    "weather_consumer_pipeline",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=20),  # hard ceiling: covers consume + write + dbt_run + dbt_test
        'on_failure_callback': on_failure_alert,
        'on_success_callback': on_success_alert,
        'on_retry_callback': on_retry_alert,
    },
    description="Weather consumer: reads Kafka → dedup-writes Snowflake WEATHER_HOURLY → dbt",
    schedule=None,  # triggered by TriggerDagRunOperator in dag_weather.py — not time-based
    start_date=pendulum.datetime(2025, 6, 8, 0, 0, tz="America/New_York"),
    catchup=False,
    tags=["weather", "kafka", "consumer", "snowflake", "learning"]
)
def weather_consumer_pipeline():
    """
    ### Weather Consumer Pipeline

    Triggered by dag_weather.py after it publishes to Kafka.
    Reads one batch from the weather.hourly.raw topic, deduplicates
    against existing Snowflake timestamps, appends new rows to
    WEATHER_HOURLY, then runs dbt marts.

    #### Pipeline stages:
    consume_from_kafka()  →  write_to_snowflake()  →  check_new_rows  →  dbt_run  →  dbt_test
    """

    @task()
    def consume_from_kafka() -> list[dict[str, Any]]:
        """
        ### Consume
        Read the latest batch from weather.hourly.raw.
        Commits offset immediately after read (before Snowflake write).
        Safe because: (a) daily batch gate prevents duplicate writes within a day,
        and (b) weather dedup logic filters already-seen timestamps before inserting.
        Polls for up to 30s then exits (DAG run already triggered, message should be present).
        """
        from kafka_client import make_consumer  # shared factory: single source of truth for consumer config
        from shared.config import KAFKA_WEATHER_TOPIC, KAFKA_WEATHER_GROUP  # deferred: centralized topic/group names

        writer: OutputTextWriter = get_writer()  # K8s PVC path or LOCAL_LOG_PATH fallback

        consumer = make_consumer(KAFKA_WEATHER_TOPIC, KAFKA_WEATHER_GROUP)  # topic/group names from shared/config.py

        records: list[dict[str, Any]] = []
        for msg in consumer:
            records.extend(msg.value)   # msg.value is list[dict] (the full batch from publish_to_kafka)
            consumer.commit()           # commit here (before Snowflake write); daily gate + timestamp dedup prevent duplicates
            writer.log(f"Consumed message offset={msg.offset}, partition={msg.partition}")

        consumer.close()
        writer.log(f"consume_from_kafka: {len(records)} records received from Kafka")
        return records


    @task()
    def write_to_snowflake(records: list[dict[str, Any]]) -> int:
        """
        ### Write
        Dedup-append records into Snowflake WEATHER_HOURLY with a daily batch gate.
        Deduplicates against existing timestamps — Open-Meteo returns 168 rows per call
        (7-day forecast window) so re-runs would insert duplicates without this check.
        Returns number of net-new rows written (0 if gate or dedup skips the write).
        """
        import pandas as pd                          # deferred: avoid slow pandas load during DAG parse
        from sqlalchemy.exc import SQLAlchemyError   # deferred: used in except clause below; kept with pandas

        writer: OutputTextWriter = get_writer()  # K8s PVC path or LOCAL_LOG_PATH fallback

        if not records:
            writer.log("write_to_snowflake: no records received from Kafka — skipping")
            return 0

        df: pd.DataFrame = pd.DataFrame(records)
        writer.log(f"write_to_snowflake: {len(df)} records to process")

        writer.log("--- Pre-insert DataFrame preview ---")
        log_df_preview(writer, df)  # shared helper: logs head() + dtypes()

        # ─── Daily Batch Gate: write to Snowflake only once per day (cost optimization) ───
        today_iso = date.today().isoformat()  # today's date as ISO string for gate comparison
        last_write = Variable.get("SF_WEATHER_LAST_WRITE_DATE", default="")  # empty string = first run; Airflow 3.x SDK raises AirflowRuntimeError on missing var (not KeyError)

        if last_write == today_iso:
            writer.log(f"Daily batch gate: already wrote today ({today_iso}) — skipping")
            return 0

        writer.log(f"Daily batch gate: last write was {last_write}, today is {today_iso} — proceeding")

        try:
            from snowflake_client import write_df_to_snowflake
            from shared.snowflake_schema import RAW_WEATHER_HOURLY, PIPELINE_DB  # deferred: centralized table/db names
            from snowflake_client import get_snowflake_cursor  # deferred: shared cursor factory

            # Schema migration: add CITY_NAME column to WEATHER_HOURLY if missing (one-time multi-city upgrade)
            sf_mig_cur = get_snowflake_cursor()
            sf_mig_cur.execute(
                f"SELECT COUNT(*) FROM {PIPELINE_DB}.INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = 'RAW' AND TABLE_NAME = 'WEATHER_HOURLY' AND COLUMN_NAME = 'CITY_NAME'"
            )
            if sf_mig_cur.fetchone()[0] == 0:
                sf_mig_cur.execute(f"ALTER TABLE {RAW_WEATHER_HOURLY} ADD COLUMN CITY_NAME VARCHAR")
                writer.log("Schema migration: added CITY_NAME column to WEATHER_HOURLY")
            sf_mig_cur.close()

            # Dedup against existing Snowflake (time, city) pairs before inserting
            sf_cur = get_snowflake_cursor()  # cursor from shared factory — no inline hook construction
            try:
                # Select both TIME and CITY_NAME to dedup on (time, city) pairs — each city can share the same timestamp
                sf_cur.execute(f"SELECT TIME, CITY_NAME FROM {RAW_WEATHER_HOURLY}")
                sf_existing = {(int(row[0]), str(row[1])) for row in sf_cur.fetchall() if row[1] is not None}
                writer.log(f"Snowflake has {len(sf_existing)} existing (time, city) pairs")
            except Exception:
                # Fall back to time-only dedup if CITY_NAME column doesn't exist yet (table schema migration)
                sf_cur.execute(f"SELECT TIME FROM {RAW_WEATHER_HOURLY}")
                sf_existing = set()  # treat all as new — first multi-city run will write all rows
                writer.log("CITY_NAME column not found — treating all rows as new (first multi-city run)")
            sf_cur.close()  # connection is managed by the hook — only the cursor needs explicit close

            # Build (epoch_seconds, city_name) pairs from the incoming DataFrame for comparison
            df_time_city = list(zip(
                pd.to_datetime(df["time"]).astype(int) // 10**9,
                df["city_name"].astype(str)
            ))
            sf_new_rows = df[[(t, c) not in sf_existing for t, c in df_time_city]].copy()
            writer.log(f"Snowflake dedup: {len(sf_existing)} existing, {len(sf_new_rows)} new rows")

            if len(sf_new_rows) > 0:
                # Cast ALL columns to match Snowflake table schema exactly
                sf_new_rows["time"] = pd.to_datetime(sf_new_rows["time"])
                sf_new_rows["imported_at"] = pd.to_datetime(sf_new_rows["imported_at"])
                sf_new_rows["temperature_2m"] = sf_new_rows["temperature_2m"].astype(float)
                sf_new_rows["latitude"] = sf_new_rows["latitude"].astype(float)
                sf_new_rows["longitude"] = sf_new_rows["longitude"].astype(float)
                sf_new_rows["elevation"] = sf_new_rows["elevation"].astype(float)
                sf_new_rows["timezone"] = sf_new_rows["timezone"].astype(str)
                sf_new_rows["utc_offset_seconds"] = sf_new_rows["utc_offset_seconds"].astype("int64")
                sf_new_rows["city_name"] = sf_new_rows["city_name"].astype(str)  # city name string — new column added for multi-city support
                write_df_to_snowflake(sf_new_rows, "WEATHER_HOURLY", overwrite=False)
                writer.log(f"Loaded {len(sf_new_rows)} rows into Snowflake WEATHER_HOURLY")
            else:
                writer.log("No new rows to insert — all timestamps already present in Snowflake")

            # Advance gate variable even if no new rows (prevents retry writes within the same day)
            Variable.set("SF_WEATHER_LAST_WRITE_DATE", today_iso)
            writer.log(f"Updated SF_WEATHER_LAST_WRITE_DATE to {today_iso}")
            return len(sf_new_rows)

        except SQLAlchemyError as e:
            writer.log(f"[ERROR] SQLAlchemy {type(e).__name__}: {e}")
            raise
        except Exception as e:
            writer.log(f"[ERROR] Unexpected {type(e).__name__}: {e}")
            raise


    # ── Wiring the pipeline ───────────────────────────────────────────────────
    records   : XComArg = consume_from_kafka()
    row_count : XComArg = write_to_snowflake(records)   # type: ignore[arg-type]

    # ShortCircuitOperator defined after row_count so op_args can reference the XComArg directly.
    # Passing row_count as op_args both supplies the value AND infers the upstream dependency.
    check_new_rows = ShortCircuitOperator(
        task_id="check_new_rows",
        python_callable=_has_new_rows,  # skip dbt if no new rows were written
        op_args=[row_count],
    )

    # dbt_run: builds STAGING views and MARTS tables in Snowflake from the freshly appended RAW data
    dbt_run = make_dbt_operator("dbt_run", "run", "weather")   # shared factory in shared/dbt_utils.py

    # dbt_test: checks not_null, unique, and accepted_values on weather models
    dbt_test = make_dbt_operator("dbt_test", "test", "weather")  # same factory, test sub-command

    check_new_rows >> dbt_run >> dbt_test  # dbt only runs if rows were actually written


dag = weather_consumer_pipeline()
