# Thread 2 — Dashboard: Pipeline Health Panel

## What was added

### 1. `dashboard/db.py` — `load_pipeline_health()`

Added a new public function that queries row counts and latest timestamps for the three core Snowflake tables in a single `UNION ALL` — one warehouse activation instead of three.

- **Tables queried:** `FCT_COMPANY_FINANCIALS` (`MAX(filed_date)`), `FCT_WEATHER_HOURLY` (`MAX(imported_at)`), `FCT_ANOMALIES` (`MAX(detected_at)`)
- **Cache TTL:** 3600s (`CACHE_TTL_FINANCIALS`) — showing health data up to 1 hour old is fine for a display-only panel
- **Guard:** returns an empty typed DataFrame when `DB_BACKEND != "snowflake"` (same pattern as `load_anomalies` and `load_weather_data`)
- Added `HEALTH_COLUMNS` constant so the guard path and the real query always return the same schema
- Added `load_pipeline_health()` to `prewarm_cache()` so the 1-hour cache entry is filled at container startup

---

### 2. `dashboard/charts.py` — `build_health_table(df)`

Added a new function that renders an HTML table with four columns: **Table**, **Row Count**, **Latest Record**, **Age (hours)**.

- **Amber highlight** (`#fffbeb`) when a row is stale: `age_hours > 25` for Financials and Anomalies (daily DAG), `age_hours > 2` for Weather (hourly DAG)
- Age computed as `(utcnow_naive - latest_ts).total_seconds() / 3600` — `tz_localize(None)` strips the tz from `pd.Timestamp.utcnow()` to match Snowflake `TIMESTAMP_NTZ` (tz-naive)
- Row counts formatted with comma separators (`f"{int(row_count):,}"`)
- Returns `html.P` placeholder when DataFrame is empty (DAG hasn't run yet)

---

### 3. `dashboard/app.py` — Pipeline Health `dcc.Loading` block

Inserted a `dcc.Loading(id="loading-health")` containing `html.Div(id="health-table")` between the Data Quality section description and the existing "Refresh Anomalies" button. The health table therefore appears at the top of the Data Quality section, above the scatter plot and anomaly detail table.

---

### 4. `dashboard/callbacks.py` — `update_health()` callback

Added `update_health()` inside `register_callbacks()`:

- **Output:** `health-table` children
- **Input:** `anomaly-refresh-btn` n_clicks — shares the existing Refresh button; no separate button means no extra warehouse trigger on every manual refresh
- `prevent_initial_call=False` — table populates on page load, same pattern as `update_anomalies`
- Updated both import lines to include `load_pipeline_health` and `build_health_table`

---

### 5. `README.md` — Cost section update

Updated two sentences in the Cost Controls section and Design Decisions section to reflect that the dashboard now runs ~4–5 Snowflake queries per hour (financials, weather, anomalies, and pipeline health — all cached 1 hour) instead of ~4.

---

## Design Decisions

**Single UNION ALL over three separate queries**
Three separate `COUNT(*) / MAX()` calls would each trigger a separate Snowflake warehouse activation. A single `UNION ALL` bundles them into one round-trip and one activation, which keeps the hourly cost impact to a single additional query on top of the existing three.

**Shared "Refresh Anomalies" button**
Dash allows multiple callbacks to share the same Input. Reusing the existing button avoids adding a second button that could confuse users and — more importantly — avoids a second independent trigger that could activate the warehouse outside the cache window.

**1-hour TTL matches financials, not weather**
Pipeline health is a display-only staleness check. Showing data that is up to 1 hour old is acceptable for this use case. Using `CACHE_TTL_WEATHER` (15 min) would have added ~4 extra warehouse cold-starts per hour with no benefit to the user.

**`filed_date` cast to `TIMESTAMP_NTZ`**
`filed_date` in `FCT_COMPANY_FINANCIALS` is a `DATE` type. `UNION ALL` requires all branches to return compatible types; casting to `TIMESTAMP_NTZ` makes it compatible with the other two `TIMESTAMP_NTZ` columns and avoids a Snowflake type-mismatch error.

---

## Snowflake Charges — Read This

This change adds **one additional cached query** per hour to the dashboard's Snowflake load.

| Trigger | Warehouse activation? |
|---|---|
| Page load (cache warm) | No |
| Page load (cache cold, first hit after 1h) | Yes — 1 additional query |
| "Refresh Anomalies" button click (cache warm) | No |
| "Refresh Anomalies" button click (cache cold) | Yes — same activation as anomalies refresh |
| Container startup pre-warm | Yes — 1 query, fires once |

Total dashboard impact: **~4–5 activations/hour** (up from ~4), regardless of user traffic.

---

## Manual Steps Required

None. This change is purely application code — no infrastructure, Terraform, or Kubernetes changes required.

Deploy the updated dashboard image using the standard deploy script when ready:

```bash
./scripts/deploy.sh
```

To verify after deploy:
1. Open the dashboard at `/dashboard/`
2. The **Pipeline Health** table should appear above the "Refresh Anomalies" button with three rows (Financials, Weather, Anomalies)
3. Any table whose latest record is older than its threshold (25h for daily, 2h for Weather) will have an amber background
4. Click "Refresh Anomalies" — both the anomaly scatter/table and the health table reload together
