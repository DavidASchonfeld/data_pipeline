# Weather Dashboard — Anomaly Detection Added

**Date:** 2026-04-15  
**Type:** New Feature

---

## What Changed

The weather dashboard page now has an "Anomaly Detection" section, matching what the stocks
dashboard page already had. Previously, the weather page showed a temperature chart and a
basic data-freshness panel. Now, below those, there is:

- A **scatter chart** showing temperature readings across all 10 cities, color-coded by city,
  with unusual readings marked with an × symbol
- A **detail table** listing every flagged reading — which city, when, what the temperature
  was, how far it was from that city's normal, and a numeric score indicating how unusual it was
- A **"Refresh Anomalies"** button to reload the latest data on demand
- The existing pipeline health panel (showing how fresh the data is) has been moved into this
  section to keep related information together

---

## How the Anomaly Detection Works

### The core idea

For each city, the system looks at the last 7 days of hourly temperature readings and asks:
*"Is this reading unusually far from what's been normal for this city lately?"*

It does this using a method called a **z-score**. Here is what that means in plain terms:

1. It calculates the **average temperature** for a city over the past 7 days.
2. It calculates how much the readings tend to **vary** day to day (the standard deviation).
3. For each hourly reading, it checks how far that reading is from the average, measured in
   units of that typical variation.
4. If a reading is more than **2 units away** from the average, it gets flagged as unusual.

As a rough example: if New York's average temperature over the past week was 55°F and readings
typically vary by about 8°F, then any reading above roughly 71°F or below roughly 39°F would
be flagged. Each city gets its own threshold based on its own recent pattern, so a 90°F reading
in Phoenix is treated differently than a 90°F reading in Seattle.

### What the scatter chart shows

The horizontal axis is the actual temperature (°F). The vertical axis is how much the temperature
changed from the previous hour. Normal readings are shown as circles. Flagged readings are shown
as × marks. This lets you see at a glance whether an unusual reading was an extreme temperature,
a sudden jump or drop, or both.

### Why not compare to the same day last year?

Comparing to the same date last year (or a multi-year seasonal average) is a strong method for
catching truly unseasonable weather — like a 90°F day in January. However, it requires at least
a full year of stored historical data.

This pipeline has been running for a matter of weeks, so there is no year-ago baseline to compare
against yet. The z-score approach makes the most of the data that actually exists right now. As
the pipeline continues collecting data over months and years, it will become possible to add
seasonal or year-over-year comparisons as an additional layer.

---

## What Was Not Changed

- The temperature chart and city selector at the top of the weather page are unchanged.
- The stocks dashboard page is unchanged.
- The underlying data pipeline (Airflow, Kafka, Snowflake) is unchanged — no new data is being
  collected and no new tables were added.
- The anomaly detection runs entirely within the dashboard server using data that was already
  being loaded. There is no additional load on the database.

---

## Who Is Affected

Anyone viewing the weather page at `/weather/` will see the new section automatically on page
load. No action is required.
