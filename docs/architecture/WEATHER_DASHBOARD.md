# Weather Dashboard

## What It Is

The Weather Dashboard is a second page in the analytics web application. It displays hourly temperature data collected from the Open-Meteo forecast API and stored in Snowflake through the existing Airflow pipeline.

It lives at `/weather/` — the same Flask server that hosts the Stocks Dashboard at `/dashboard/`. Both pages are linked to each other through navigation links at the top of each page.

---

## What You Can See

- **City Selector** — A dropdown to pick from the top 10 US cities by population. Switching cities updates the chart instantly — no additional database queries are made.
- **7-Day Temperature Chart** — A line chart showing hourly temperature readings (in Fahrenheit) for the selected city over the last 7 days. The x-axis is time; the y-axis is temperature.
- **Stats Summary Table** — A single-row table showing:
  - Current temperature (latest reading)
  - 24-hour minimum temperature
  - 24-hour maximum temperature
  - City name and location (latitude/longitude)
  - Elevation (meters)
  - Timezone (IANA string)
- **Refresh Button** — Clicking "Refresh Weather" re-queries Snowflake and re-renders the charts. Data is also loaded automatically when the page first opens.
- **Pipeline Health Panel** — Shows row count and data freshness for the weather table, so you can see at a glance whether the pipeline ran recently.

---

## Data Source

- **API**: [Open-Meteo](https://open-meteo.com/) — free, no API key required
- **Locations**: Top 10 US cities by population — New York, Los Angeles, Chicago, Houston, Phoenix, Philadelphia, San Antonio, San Diego, Dallas, Austin
- **Frequency**: Hourly forecast, 7 days per city (168 rows per API call × 10 cities = ~1,680 rows per DAG run)
- **Unit**: Temperature in Fahrenheit

---

## How the Data Gets to the Dashboard

```
Open-Meteo API
    ↓  (dag_weather.py — runs hourly via Airflow)
Kafka topic: weather-hourly-raw
    ↓  (dag_weather_consumer.py — triggered by producer DAG)
Snowflake: PIPELINE_DB.RAW.WEATHER_HOURLY
    ↓  (dbt: stg_weather_hourly — staging view)
Snowflake: PIPELINE_DB.STAGING.STG_WEATHER_HOURLY
    ↓  (dbt: fct_weather_hourly — deduplication + materialization)
Snowflake: PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY
    ↓  (load_weather_data() in db.py)
Weather Dashboard at /weather/
```

The pipeline writes data at most once per day (cost optimization: daily batch gate via Airflow Variable `SF_WEATHER_LAST_WRITE_DATE`). The dashboard reads the last 7 days of data.

---

## Snowflake Table: FCT_WEATHER_HOURLY

| Column | Type | Description |
|--------|------|-------------|
| `observation_time` | TIMESTAMP_NTZ | When the temperature was recorded |
| `city_name` | STRING | City name (e.g. "New York") — composite key with `observation_time` |
| `temperature_f` | FLOAT | Temperature in Fahrenheit at 2-meter height |
| `latitude` | FLOAT | Location latitude |
| `longitude` | FLOAT | Location longitude |
| `elevation` | FLOAT | Location elevation in meters |
| `timezone` | STRING | IANA timezone string (e.g. `America/New_York`) |
| `utc_offset_seconds` | INTEGER | UTC offset in seconds |
| `imported_at` | TIMESTAMP_NTZ | When the row was loaded into Snowflake |

Rows are unique per `(observation_time, city_name)` pair — the same timestamp appears once per city, so the primary key is composite.

---

## Caching

Weather data is cached in memory for **15 minutes** (`CACHE_TTL_WEATHER = 900` in `db.py`). This prevents a Snowflake query on every page load. The cache is also pre-warmed at Flask startup in the same background thread that pre-warms the financials and anomaly data.

---

## Code Files

| File | Role |
|------|------|
| `dashboard/app.py` | Mounts the weather Dash app at `/weather/`; defines the page layout including city dropdown and health panel |
| `dashboard/weather_charts.py` | Chart builders: `build_temperature_fig()`, `build_weather_stats_table()` |
| `dashboard/callbacks.py` | `register_weather_callbacks()` — wires city dropdown, refresh button, and health panel |
| `dashboard/db.py` | `load_weather_data()` — queries FCT_WEATHER_HOURLY (all cities) with 15-min cache; `load_weather_health()` — freshness panel |
| `dashboard/security.py` | `ALLOWED_CITIES` — allowlist of valid city names to prevent arbitrary input |
| `airflow/dags/dag_weather.py` | Producer DAG — fetches all 10 cities from Open-Meteo, publishes to Kafka |
| `airflow/dags/dag_weather_consumer.py` | Consumer DAG — Kafka → Snowflake RAW → dbt; deduplicates per (time, city) |
| `airflow/dags/dbt/models/marts/fct_weather_hourly.sql` | dbt model — deduplication per (observation_time, city_name), final fact table |
| `airflow/dags/shared/config.py` | `WEATHER_CITIES` dict — city names mapped to (latitude, longitude) coordinates |

---

## Navigation

- From the Stocks Dashboard (`/dashboard/`): click **"View Weather Dashboard →"** at the top of the page.
- From the Weather Dashboard (`/weather/`): click **"← View Stocks Dashboard"** at the top of the page.

---

## Deployment

No deploy script changes are needed. The weather dashboard is part of the Flask/Dash application container image. Running `./scripts/deploy.sh` rebuilds and pushes the image, then restarts the pod with the new code — the weather page will be available automatically after the next deploy.
