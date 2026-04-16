# "Callback failed" Appearing Twice — Reload Loop During Weather Page Navigation

**Date:** 2026-04-15
**Severity:** Medium — navigating from the stocks page to the weather page caused the browser to silently reload once, producing a duplicate "Callback failed" error in the console and briefly breaking the page
**Status:** Fixed

---

## What Happened

A user clicked "View Weather Dashboard →" on the stocks page. The weather page loaded, but the charts and tables were briefly blank. The browser's developer console showed the same error message twice:

```
[Error] Error: Callback failed: the server did not respond.
[Error] Error: Callback failed: the server did not respond.
```

The page appeared to flicker — as if it reloaded itself — before settling. In the worst case (if data remained slow on the second load), the page would keep reloading in a loop.

---

## Root Cause

### A previous fix introduced the loop

An earlier incident ([`2026-04-15-weather-nav-callback-failed.md`](2026-04-15-weather-nav-callback-failed.md)) added a `console.error` interceptor to the recovery script (`recovery.js`). Its job: detect when Dash silently reports a failed data request and trigger the "Reconnecting…" overlay.

The interceptor worked correctly for one scenario — server is truly down — but had an unintended side effect for a different scenario: **server is healthy but a query was slow**.

### What happens when you navigate to the weather page

When the weather page loads, it fires three data requests at the same moment:
- Temperature chart
- Weather health table
- Anomaly detection chart

If the Snowflake data warehouse has been idle, it needs a few seconds to wake up before it can respond. During that window, one or more of these requests may time out and fail. Dash catches the failure internally and shows an error message inside the chart — the page is not blank, the server is still running.

### The loop

The `console.error` interceptor saw the "Callback failed" message and started its recovery flow:

1. Wait 3 seconds (to give Dash a chance to retry on its own).
2. Ask the server: are you healthy?
3. Server says **yes** (it was never down — just the query was slow).
4. Previous behavior: **reload the page**.

Reloading the page fires the same three data requests again. If the warehouse is still waking up, they fail again. The interceptor fires again. Another reload. This is the loop.

The error appearing **twice** in the console is the fingerprint of one loop iteration: the original page load plus the first reload both failed.

---

## What Was Fixed

### 1. Grace period for errors during initial page load (`recovery.js`)

A 15-second window now starts when the page first loads. During this window, "Callback failed" errors are **ignored by the recovery script** entirely.

Why 15 seconds? A Snowflake warehouse typically takes 10–30 seconds to wake up after being idle. 15 seconds covers the common case. During this window, Dash already shows an error message ("Data temporarily unavailable") directly inside the chart panel, and a "Refresh" button is available. The user is not left staring at a blank page.

After 15 seconds, the full recovery flow resumes — so mid-session server loss is still caught and handled correctly.

### 2. Callback errors with a healthy server no longer trigger a reload (`recovery.js`)

Even after the grace period, if the recovery flow determines that the server is healthy, it now checks *what kind of error* was detected:

- **True crash** (blank page, Redux error, or the browser failing to parse the server's response): reload the page. A clean reload is the right recovery here.
- **Callback failure** (Dash reported a query failure but the server responded normally): do **not** reload. Dash already showed the error in the component. The user can click Refresh when ready.

This prevents the loop entirely: reloading is only triggered when the page is genuinely broken, not when a single data query was slow.

### 3. Snowflake query timeout added (`db.py`)

Previously, `login_timeout=10` capped only the time to authenticate with Snowflake (the sign-in step). Once authenticated, a query could run for any amount of time — including waiting for a warehouse to resume from idle.

A new session setting (`STATEMENT_TIMEOUT_IN_SECONDS = 60`) now limits how long any single query can run. After 60 seconds, Snowflake cancels it, the server logs the failure, and Dash displays the error in the component. This prevents Gunicorn workers from being permanently blocked, which would eventually cause all page requests to fail.

---

## Result

| Scenario | Before | After |
|---|---|---|
| Navigate to weather, warehouse waking up | Reload loop, "Callback failed" ×2 in console | Grace period suppresses recovery; Dash shows error in component; user clicks Refresh |
| Navigate to weather, server genuinely down | Overlay appeared (correct) | Overlay still appears (unchanged) |
| Snowflake query hangs indefinitely | Workers blocked forever | Query cancelled after 60 s; error shown in component |

---

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/assets/recovery.js` | Added 15-second grace period after page load; callback failures with a healthy server no longer trigger a page reload |
| `dashboard/db.py` | Added `STATEMENT_TIMEOUT_IN_SECONDS: 60` Snowflake session parameter alongside the existing `login_timeout` |
