# How Long Until Data Appears on the Dashboard?

A plain-English guide to what happens after you manually trigger a pipeline run, and when you can expect to see the results.

---

## The Short Answer

| Pipeline | Time from trigger to visible data |
|---|---|
| Weather | ~1 minute |
| Stocks & Financials | ~1–2 minutes |

If you trigger a pipeline and then **refresh the dashboard page**, data should appear within the times above — assuming this is the first time the pipeline has run today (see [the daily limit](#the-daily-limit-one-run-per-day) below).

---

## Weather Pipeline

### What happens after you click "Trigger"

1. **The pipeline fetches weather forecasts** for 10 US cities from a weather service. This takes about 20 seconds — it makes one request per city with a small pause between each to be polite to the server.

2. **The data is briefly stored in a message queue** (takes about 2 seconds), and a second part of the pipeline immediately picks it up.

3. **The data is written to the database** — but only rows that haven't been stored yet are saved. Open-Meteo sends a 7-day forecast each time, so most rows on a re-run are already there from the previous run. Only genuinely new hours get added (~5 seconds).

4. **The data warehouse rebuilds its summary tables** — this is the step that makes the data "dashboard-ready." It takes about 15–20 seconds.

5. **You refresh the dashboard** and the new data is there.

**Total time from trigger to dashboard: roughly 50–60 seconds.**

### After the dashboard shows data

The dashboard keeps a copy of recently-fetched data in memory for **15 minutes** to avoid hammering the database on every page load. This means:

- If you reload the dashboard shortly after data appears, it's instant.
- If the pipeline runs again within those 15 minutes, the dashboard won't yet reflect the newest data — it will still show the cached version. After 15 minutes, the next page load will pull fresh data automatically.

### Automatic schedule

The weather pipeline runs automatically **every hour** — you don't need to trigger it manually under normal operation. After the first manual trigger (to fill an empty dashboard), the pipeline will keep the data current on its own.

---

## Stocks & Company Financials Pipeline

### What happens after you click "Trigger"

1. **The pipeline fetches financial filings** for three companies (Apple, Microsoft, Google) from the SEC's public database. The SEC limits how fast you can request data, so this takes about 20 seconds.

2. **The data is tidied up** — the raw SEC filings are in a complex nested format. This step flattens them into a clean table (~3 seconds).

3. **The data travels through the message queue** to a second part of the pipeline (~2 seconds).

4. **The data is written to the database.** Unlike weather, stocks data is a full historical record — every annual filing going back decades. The pipeline replaces the whole table each time (~5 seconds).

5. **The data warehouse rebuilds its summary tables** (~15–20 seconds).

6. **Anomaly detection runs** — a small statistical model scans the financial data for unusual year-over-year changes (e.g., a sudden revenue spike or drop). It writes its findings to a separate table. This takes about 10–15 seconds.

7. **You refresh the dashboard** and the charts and anomaly flags are populated.

**Total time from trigger to dashboard: roughly 60–75 seconds.**

### After the dashboard shows data

The dashboard caches stocks and anomaly data in memory for **1 hour**. This means:

- Page loads are fast after the first load.
- If you trigger the pipeline again within the hour, the dashboard won't immediately reflect it — you'd need to wait for the cache to expire (up to 1 hour) before a page refresh shows the newest run's results. In practice this doesn't matter much, since the financial data only changes once per day anyway.

### Automatic schedule

The stocks pipeline runs automatically **once per day**. It pulls SEC EDGAR filings which are only updated on business days when companies file, so daily is the right frequency.

---

## The Daily Limit: One Run Per Day

Both pipelines have a built-in safeguard: **they will only write new data to the database once per calendar day**, even if you trigger them multiple times.

This prevents the same data from being stored twice, which would cause duplicate rows and incorrect charts.

**What this means in practice:**

- **First trigger of the day on an empty dashboard:** Everything runs, data is written, charts appear. ✓
- **Second trigger on the same day:** The pipeline runs, fetches data from the source, but when it checks the database it sees data was already written today — so it skips the write step and the charts don't change. This is intentional, not a bug.
- **If you need to force a re-run** (e.g., you cleared the database): this requires resetting the pipeline's internal "already ran today" flag in Airflow. Ask someone with Airflow access.

---

## What "Empty Dashboard" Means

If you open the dashboard and the charts are blank, it means one of two things:

1. **The pipeline hasn't run yet today** — trigger it manually and wait ~1–2 minutes.
2. **The dashboard is still loading its data from the database** — there's a brief spinner while it fetches data on first load. This usually takes 3–5 seconds and then the charts appear.

If charts are still blank after waiting 2–3 minutes and refreshing the page, the pipeline may have encountered an error during its run. Check the Airflow UI for task status.

---

## Quick Reference

| Thing you want to know | Answer |
|---|---|
| Weather: time from trigger to data visible | ~1 minute |
| Stocks: time from trigger to data visible | ~1–2 minutes |
| How often weather runs automatically | Every hour |
| How often stocks runs automatically | Once per day |
| Can I trigger the same pipeline twice in one day? | Yes, but only the first run writes new data |
| How long does the dashboard cache data? | 15 min (weather) / 1 hour (stocks) |
| What to do if charts are still blank after 3 minutes | Check Airflow for errors |
