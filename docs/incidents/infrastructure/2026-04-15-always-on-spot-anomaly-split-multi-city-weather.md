# Always-On Server, Split Health Panels, Multi-City Weather — April 15, 2026

**Date:** 2026-04-15
**Severity:** Low (improvements)
**Affected components:** EC2 infrastructure, Weather DAG, Dashboard

---

## What was the problem

Three separate issues were addressed in this update — none of them were broken in the critical sense, but each one made the project harder to use or understand.

**1. The server kept going to sleep, making the dashboard slow to load.**
To save money, the server was set up to automatically shut itself down after 45 minutes with no activity. When someone visited the dashboard, it would wake the server back up — but that startup process took around 4 minutes. Visitors would see a loading screen with a misleading countdown timer instead of the actual dashboard.

**2. The stock page showed weather health data, and the weather page showed nothing.**
Both pages had a "Pipeline Health" section — a small table showing how recently the data was last updated (called "freshness"). The stock page was listing freshness for every table in the pipeline, including the weather table, which has nothing to do with stocks. Meanwhile, the weather page had no health information at all.

**3. The weather data came from a single arbitrary location in Turkey.**
The original weather data was pulled from coordinates that pointed to a spot on the Black Sea coast of Turkey (latitude 40°N, longitude 40°E). This was a placeholder chosen purely for learning purposes during the early build. There was no way to view data for any real, meaningful location.

---

## What was changed

### 1. Removed the sleep/wake cycle — server now stays on 24/7

The automatic shutdown feature has been removed. The server now runs continuously.

This is affordable because the server already uses "spot pricing" — AWS's discount program where you use spare computing capacity at 70-80% off in exchange for the possibility of AWS reclaiming it with a 2-minute warning. That self-healing mechanism (an Auto Scaling Group that automatically replaces a reclaimed server) was already in place from a previous update. With spot pricing, the server is cheap enough to leave running all the time.

When AWS does reclaim the server (which happens rarely), the dashboard now shows a toast notification — a small pop-up banner at the bottom of the screen — with a live countdown timer so visitors know a replacement is on its way and roughly when it will be ready.

**Result:** The dashboard is always immediately accessible. The 4-minute wait is gone.

Files changed:
- `terraform/main.tf` — removed the Lambda function and API Gateway that handled the sleep/wake mechanism
- `dashboard/app.py` — added a `/spot-interruption` webhook endpoint that the server triggers before shutting down
- `dashboard/callbacks.py` — added the toast notification with countdown timer

### 2. Split the Pipeline Health section by page

The "Pipeline Health" freshness table is now tailored to each page's content.

- **Stock page**: shows freshness for the Financials table and the Anomalies table only
- **Weather page**: now has its own Pipeline Health section showing freshness for the Weather table only

Each page now only shows information relevant to what it's displaying.

Files changed:
- `dashboard/db.py` — added a separate query function for weather table freshness
- `dashboard/callbacks.py` — updated each page's health callback to pull from the appropriate query
- `dashboard/charts.py` — added a weather-specific health table figure

### 3. Expanded weather data from 1 location to 10 US cities

The weather pipeline now collects data for the 10 most populous US cities: New York, Los Angeles, Chicago, Houston, Phoenix, Philadelphia, San Antonio, San Diego, Dallas, and Austin.

Every hour, the pipeline fetches fresh weather readings for all 10 cities and stores them in Snowflake (the cloud data warehouse the project uses). A dropdown menu on the weather dashboard lets visitors switch between cities.

**An important note on cost:** All 10 cities are fetched and stored together in a single hourly pipeline run. The dashboard then filters to the selected city using data that's already been retrieved — it does not go back to the database for each city switch. This means switching cities in the dropdown is instant and adds no extra database cost. Snowflake costs remain the same whether users explore one city or all ten.

Files changed:
- `airflow/dags/dag_weather.py` — replaced the single hardcoded coordinate with a list of 10 cities and their coordinates; each city is now tagged with a `city` label when stored
- `airflow/dags/dbt/models/marts/fct_weather_hourly.sql` — updated to include the `city` column
- `dashboard/db.py` — updated weather query to return all cities at once
- `dashboard/callbacks.py` — added city dropdown callback; filters locally rather than re-querying
- `dashboard/app.py` — added city dropdown component to the weather page layout
