# Dashboard Cache — How It Works (Plain English)

## The Problem It Solves

Every time the dashboard needs to show data, it has to ask Snowflake for it. Snowflake is slow (a few seconds per query) and costs money every time the warehouse wakes up to run a query. The cache solves both problems: ask Snowflake once, store the answer in memory, serve every subsequent request from memory instead.

---

## Where the Cache Lives

The cache is a Python dictionary (a simple key→value store) sitting in RAM inside the Flask/Dash container. It is defined in `dashboard/db.py`:

```python
_QUERY_CACHE: dict = {}  # {key: (dataframe, expires_at)}
```

There is no Redis, no database, no files on disk — just a dict in memory. Fast, simple, zero extra infrastructure.

---

## How It Works Step by Step

### 1. A user loads the dashboard

Dash fires the callbacks (`update_charts`, `update_anomalies`). Those callbacks call the query functions in `db.py`.

### 2. The query function checks the cache first

Before touching Snowflake, every query function does this:

```
Is there a result in the cache for this key?
  YES → Is it still fresh (not expired)?
    YES → Return it immediately. Done. Snowflake never contacted.
    NO  → Fall through and query Snowflake.
  NO  → Fall through and query Snowflake.
```

### 3. If the cache is empty or stale, Snowflake is queried

The function runs the SQL query, waits for Snowflake to respond (~3–5 seconds), gets the data back as a DataFrame, and then:

```
Store the result in the cache with a timestamp of "expires in 1 hour from now"
Return the result to the callback
```

### 4. The next user (within 1 hour) gets instant results

The cache has the data. Snowflake is never contacted. The page loads instantly.

---

## Cache Keys and TTLs

| Data | Cache Key | Time-to-Live |
|---|---|---|
| Financials for AAPL | `financials:AAPL` | 1 hour |
| Financials for MSFT | `financials:MSFT` | 1 hour |
| Financials for GOOGL | `financials:GOOGL` | 1 hour |
| Anomaly scores | `anomalies` | 1 hour |
| Weather data (all 10 cities) | `weather` | 15 minutes |
| Stock pipeline health | `stock_health` | 1 hour |
| Weather pipeline health | `weather_health` | 15 minutes |

**Why different TTLs?** SEC filings and anomaly scores change at most once per day, so 1 hour is sufficient. Weather data updates every hour from the Open-Meteo API, so 15 minutes keeps the display reasonably fresh without over-querying Snowflake.

**City switching is free.** All 10 cities are loaded into the `weather` cache entry as one combined dataset. When a user picks a different city in the dropdown, the city is filtered from the cached data in Python — no extra Snowflake query happens.

---

## The Pre-Warm: Why It Exists

The cache starts **completely empty** every time the container starts (after a deploy, after a crash, after a restart). Without pre-warming, the very first user to load the page after a restart would sit through the 3–5 second Snowflake delay.

The pre-warm fixes this. The moment the container boots, a background thread immediately runs all the Snowflake queries and fills the cache. This happens in the background — the container is ready to serve requests immediately, and the pre-warm runs in parallel. By the time a real user loads the page, the cache is already hot.

```
Container starts
    │
    ├─► Gunicorn starts serving requests (immediately)
    │
    └─► Background thread: query Snowflake for all tickers, anomalies, weather (all cities), and both health panels
            │
            └─► Cache is now populated (takes ~30–60 seconds — all 7 queries run in parallel)

User opens dashboard (usually after the pre-warm has finished)
    └─► Served from cache instantly
```

The `dcc.Loading` spinner (on the dashboard page) covers the rare case where someone opens the page in the few seconds before the pre-warm finishes.

---

## What the Cache Does NOT Do

- **It does not survive a container restart.** The dict lives in RAM. When the container stops (every deploy), the dict is gone. The pre-warm repopulates it on the next startup.
- **It is not shared between Gunicorn workers.** If Gunicorn runs multiple worker processes, each worker has its own separate cache dict. Each worker does its own pre-warm independently on first load. For this project (1 replica, small traffic), this has no practical impact.
- **It does not automatically refresh in the background.** After 1 hour the TTL expires and the next request that comes in will trigger a fresh Snowflake query. There is no background "refresh on a timer" — the refresh is demand-driven.

---

## Snowflake Cost Impact

Each Snowflake query runs for ~3–5 seconds on an XS warehouse. The XS warehouse costs roughly $2/credit, and 1 credit = 1 hour of warehouse time. A 5-second query costs about $0.003. With the cache:

- **Without cache:** Every page load hits Snowflake → cost scales with traffic
- **With cache + pre-warm:** ~7 Snowflake queries per container restart (one per ticker, plus anomalies, weather, stock health, and weather health) + a small number per hour for TTL refresh → cost is nearly flat regardless of traffic

For this project the absolute dollar amounts are tiny, but the pattern is the correct one even at larger scale.

---

## Files Involved

| File | What it does |
|---|---|
| `dashboard/db.py` | Defines `_QUERY_CACHE`, `_cache_get`, `_cache_set`, `prewarm_cache`; all query functions (`load_anomalies`, `load_weather_data`, `load_stock_health`, `load_weather_health`) go through the cache |
| `dashboard/app.py` | Fires `prewarm_cache` in a background thread at startup |
| `dashboard/callbacks.py` | Calls the query functions in `db.py` (which hit the cache before touching Snowflake) |
