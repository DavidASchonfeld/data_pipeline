# General Libraries

import os
import json
from typing import Any
from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, task, XComArg, get_current_context, Variable  # Airflow 3.x SDK — replaces airflow.decorators and airflow.models.xcom_arg
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator  # fires consumer DAG after publish

# My Files
from weather_client import fetch_weather_forecast  # renamed from sendRequest_openMeteo
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from shared.utils import get_writer, log_df_preview  # shared log writer factory and DataFrame preview helper
from dag_utils import check_vacation_mode  # shared guard: skips task if VACATION_MODE Variable is "true"
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts on task failure/retry/recovery


# ── Why Open-Meteo instead of OpenWeatherMap? ────────────────────────────────
# Open-Meteo (api.open-meteo.com) is completely free with no API key required.
# The original version used OpenWeatherMap (archived in _archive/old_openWeatherMap.py),
# but it required a paid plan for hourly data. Open-Meteo provides hourly forecasts
# at no cost and with no rate limits — ideal for learning and practice.
#
# Schedule: hourly (matching Open-Meteo's own forecast refresh rate).
#   Open-Meteo returns 168 rows per call (7 days × 24 hours). Running more frequently
#   than once per hour would fetch identical data and create duplicate rows.
#   The deduplication logic in load() guards against this, but hourly is the correct cadence.
# ─────────────────────────────────────────────────────────────────────────────


@dag(  # type:ignore
    "API_Weather-Pull_Data",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=10),  # hard ceiling: kills task if it hangs past this
        'on_failure_callback': on_failure_alert,  # Slack + PVC log on task failure
        'on_success_callback': on_success_alert,  # Slack recovery message + clear alert state
        'on_retry_callback': on_retry_alert,  # Slack + PVC log on task retry
    },
    description="Weather pipeline: Open-Meteo → Kafka (consumer DAG writes Snowflake → dbt)",
    schedule=timedelta(hours=1),  # Hourly: Open-Meteo refreshes its forecast data once per hour
    # Use fixed past date instead of pendulum.now() to prevent DAG configuration drift on each parse
    start_date=pendulum.datetime(2025, 6, 8, 0, 0, tz="America/New_York"),
    # Note: start_date has to be in the past if you want it to run today/later
    catchup=False,
    tags=["learning","weather","external api pull"]
)

def weather_pipeline():
    """
    ### Weather Data Pipeline

    Pulls hourly temperature forecasts from Open-Meteo for the top 10 US cities
    (by population) and loads them into Snowflake (RAW schema, table: WEATHER_HOURLY)
    once per day via batch gate. All cities are fetched upfront each run so the
    dashboard dropdown never needs to query Snowflake per city click.

    #### Pipeline stages:
    extract()  →  transform()  →  publish_to_kafka()  →  trigger weather_consumer_pipeline
    (Snowflake write + dbt run in dag_weather_consumer.py)
    """

    @task()
    def extract():
        """
        ### Extract:
        Pull hourly forecasts from Open-Meteo for all 10 cities.
        """

        # Halt this task (and downstream transform/load) if vacation mode is active
        check_vacation_mode()

        import time  # deferred: used only here for API courtesy delay between city requests
        from shared.config import WEATHER_CITIES  # deferred: top 10 US cities with coordinates

        all_records = []
        for city_name, (lat, lon) in WEATHER_CITIES.items():
            raw_data = fetch_weather_forecast(latitude=lat, longitude=lon, fahrenheit=True)
            if not all(key in raw_data for key in ["hourly", "hourly_units"]):
                raise ValueError(f"API response missing required keys for city: {city_name}")
            if "temperature_2m" not in raw_data["hourly"]:
                raise ValueError(f"API response missing 'temperature_2m' for city: {city_name}")
            # Tag each record with the city name so Snowflake can filter per-city
            raw_data["city_name"] = city_name
            all_records.append(raw_data)
            time.sleep(0.2)  # brief pause between API calls — courtesy to the free Open-Meteo service
        return all_records


    # @task(multiple_outputs=True)
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])
    @task()
    def transform(all_cities_data):
        import pandas as pd  # deferred: avoid slow pandas init during DagBag parse

        writer: OutputTextWriter = get_writer()  # K8s PVC path or LOCAL_LOG_PATH fallback

        all_dfs = []
        for raw_data in all_cities_data:
            city_name = raw_data["city_name"]  # city name tagged by extract() task
            df = pd.DataFrame({
                "time"              : raw_data["hourly"]["time"],
                "temperature_2m"   : raw_data["hourly"]["temperature_2m"],
                "latitude"         : raw_data["latitude"],
                "longitude"        : raw_data["longitude"],
                "elevation"        : raw_data["elevation"],
                "timezone"         : raw_data["timezone"],
                "utc_offset_seconds": raw_data["utc_offset_seconds"],
                "city_name"        : city_name,  # city identifier — used for dashboard dropdown filtering
                "imported_at"      : datetime.now().isoformat(),  # audit column: when this row was loaded
            })
            all_dfs.append(df)

        combined_df = pd.concat(all_dfs, ignore_index=True)  # merge all city DataFrames into one batch
        writer.log("----Transform Preview----")
        log_df_preview(writer, combined_df)  # shared helper: logs head() + dtypes()

        # Convert to list-of-dicts so Airflow XCom can serialize it as JSON
        return combined_df.to_dict(orient="records")

    @task()
    def publish_to_kafka(records: list[dict[str, Any]]) -> int:
        """
        ### Publish
        Publish the transformed hourly records to Kafka topic weather.hourly.raw.
        Returns record count. The consumer DAG (dag_weather_consumer.py) handles
        the Snowflake dedup write and dbt run.

        One message per DAG run keyed by run_id for idempotency.
        """
        from kafka_client import make_producer  # shared factory: single source of truth for producer config
        from shared.config import KAFKA_WEATHER_TOPIC  # deferred: centralized topic name

        writer: OutputTextWriter = get_writer()  # K8s PVC path or LOCAL_LOG_PATH fallback
        context = get_current_context()

        producer = make_producer()  # construct producer with broker address resolved from Airflow Variable

        # Single message per run — full list-of-dicts as one JSON payload
        producer.send(
            KAFKA_WEATHER_TOPIC,
            key=context["run_id"].encode("utf-8"),  # idempotency key: prevents duplicate processing on retry
            value=records,
        )
        producer.flush()   # block until broker acknowledges receipt
        producer.close()

        writer.log(f"Published {len(records)} records to {KAFKA_WEATHER_TOPIC}")
        return len(records)

    # Airflow automatically converts all task method return values to XComArg objects for cross-task data passing.

    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # extract → transform → publish_to_kafka → trigger consumer DAG
    # Snowflake write + dbt are handled in dag_weather_consumer.py
    all_cities_data : XComArg = extract()
    records         : XComArg = transform(all_cities_data)
    publish_task          = publish_to_kafka(records)  # type: ignore[arg-type]

    # Fire consumer DAG after publish; consumer owns Snowflake write + dbt
    trigger_consumer = TriggerDagRunOperator(
        task_id="trigger_consumer",
        trigger_dag_id="weather_consumer_pipeline",
        wait_for_completion=False,  # fire-and-forget — consumer DAG has its own retries
    )
    publish_task >> trigger_consumer

dag = weather_pipeline()  # assign to module-level variable — Airflow best practice for DAG discovery
