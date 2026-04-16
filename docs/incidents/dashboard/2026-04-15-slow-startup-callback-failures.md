# Incident: Slow graph load + "Callback failed" errors after startup

**Date:** 2026-04-15
**Severity:** Medium — dashboard was usable but required a manual refresh and showed errors
**Status:** Fixed

---

## What happened

When visiting the dashboard after a cold start (server just switched to a new spot instance):

1. The "Loading…" page appeared for about 60 seconds — *this is normal and expected* while the server fetches all data from the database.
2. Once the loading page redirected to the dashboard, graphs and tables took **25 seconds** to appear.
3. The browser console showed **"Callback failed: the server did not respond"** errors, meaning some graph/table requests were silently dropped.
4. The recovery overlay from previous fixes would catch these errors, wait a moment, and reload the page — sometimes triggering a second attempt before everything settled.

---

## Why it happened

### How the server is structured

The dashboard runs on **Gunicorn**, a web server that launches **2 worker processes** to handle requests in parallel. Think of them as two cashiers at a checkout — if one is busy, the other picks up the next customer.

When the server starts, each worker independently begins loading data from Snowflake (our database) into its own local memory. This loading process is called "pre-warming the cache." Once the cache is warm, every user request gets answered instantly from memory instead of going to the database.

### The flaw

There was a race between the loading page and the workers:

1. **Worker A** finished warming its cache first.
2. The loading page noticed Worker A was ready and redirected the user to the dashboard.
3. **Worker B** was *still loading* its own cache at this point.
4. When the dashboard fired multiple requests at once (one per chart and table), they were split between Worker A and Worker B.
5. Requests that landed on **Worker B** had no cached data, so Worker B went to Snowflake directly.
6. Snowflake's warehouse had been asleep since the last time data was fetched. Waking it up takes 30–60 seconds.
7. Multiple requests queued up behind that wake-up time. The total wait exceeded the server's 120-second safety timeout, causing the server to abandon those requests — producing the **"Callback failed"** errors.

### Why the cascade happened

Because both workers were also pre-warming in the background *at the same time* the user was sending requests, they were competing with each other for the slow, waking Snowflake warehouse. This made everything slower than it needed to be.

---

## The fix

The fix changes **when** and **how many times** the data is pre-loaded:

**Before:** Each of the 2 workers loaded data independently in the background after starting. The loading page could redirect the user before both workers were ready.

**After:** The server now loads all data **once**, before any workers start. Only then are workers created — and they all begin with the same hot, pre-loaded data already in memory. When the loading page redirects the user, *every* worker is guaranteed to be ready. The first request hits the in-memory cache and returns in under a second.

This is achieved with two changes to how Gunicorn (the web server) is configured:

- **`--preload`** — tells Gunicorn to run the startup code (including the data fetch) once before creating workers, not once per worker.
- **`gunicorn.conf.py`** — a small config file with a "post-fork hook": after each worker is created, it discards any inherited database connections and creates its own fresh ones. This is a safety measure because sharing database connections across processes is not safe.

---

## Impact of the fix

| Metric | Before | After |
|---|---|---|
| Loading page duration | ~60 s | ~60 s (unchanged) |
| Graph/table load time after redirect | ~25 s | < 1 s |
| "Callback failed" console errors | Present | Eliminated |

The loading page duration is unchanged — the database still needs time to wake up, and the data fetch still takes the same time. The difference is that this wait now happens *before* the user sees the dashboard, rather than *after*.

---

## Files changed

- `dashboard/app.py` — removed background-thread prewarm; now runs synchronously before Gunicorn forks
- `dashboard/Dockerfile` — added `--preload` and `--config /app/gunicorn.conf.py` flags to Gunicorn
- `dashboard/gunicorn.conf.py` — new file; post-fork hook to dispose inherited DB connections per worker
