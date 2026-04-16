# Dashboard Goes Blank During Server Replacement

**Date:** 2026-04-15
**Severity:** High — dashboard becomes completely unusable (blank page) for all visitors during server switches

## What Happened

While browsing the dashboard, the weather page gradually stopped showing data. Navigating to the stocks page produced a completely blank screen (just the dark background color). Refreshing sometimes showed the "Switching servers" loading page, and other times returned to a blank page. The browser console showed errors about receiving the wrong type of data.

## Root Cause

This dashboard runs on a discounted "spare capacity" server (EC2 Spot Instance). When Amazon reclaims the server, a new one boots automatically. During that transition, a content delivery network (CloudFront) sits between visitors and the server. It has a safety feature: if the server is unreachable, show a friendly "Switching servers" loading page instead of a raw error.

The problem is that the dashboard has two types of requests:

1. **Page loads** (visiting the URL) — these expect an HTML web page. The "Switching servers" page works perfectly here because the browser knows how to display HTML.

2. **Data updates** (chart data, anomaly scores, table contents) — these happen in the background after the page loads and expect structured data (JSON), not HTML. When the server goes down mid-session, these data requests also receive the "Switching servers" HTML page. The dashboard's charting library tries to read this HTML as data, can't understand it, and crashes — wiping the entire page blank.

Once the page goes blank, there is no recovery mechanism. The existing "Dashboard temporarily offline" warning banner is part of the dashboard itself, so when the dashboard crashes, the banner is destroyed along with everything else.

A secondary issue: after a server replacement, database connections left over from the old server become stale. The dashboard was not checking whether these connections were still alive before using them, which could cause requests to hang indefinitely.

## How It Was Identified

Observed directly: the weather dashboard partially stopped working, the stocks page went fully blank, and the browser developer console showed JavaScript errors ("Minified Redux error #14" and "Response is missing header: content-type: application/json"). These errors confirmed that the charting library was receiving HTML where it expected JSON data.

## What Was Fixed

### 1. Automatic crash recovery script (`dashboard/assets/recovery.js`)

A new standalone script that runs independently of the dashboard's charting library. Because it is separate, it survives when the charting library crashes. When it detects a crash:

- It covers the blank page with a "Reconnecting..." overlay (matching the dashboard's dark theme)
- It checks the server's health endpoint every 5 seconds in the background
- Once the server reports it is fully ready (data cache is warm), the page automatically reloads

This means visitors now see a clear "Reconnecting" message instead of a blank screen, and the page recovers on its own without manual refreshing.

### 2. Database connection health checks (`dashboard/db.py`)

Added two settings to the database connection:

- **Connection verification** — before each database query, the system now checks that the connection is still alive. If it finds a stale connection left over from the old server, it silently replaces it with a fresh one instead of hanging.
- **Connection timeout** — if the database is completely unreachable, the connection attempt gives up after 10 seconds instead of waiting indefinitely.

### 3. Improved server status detection (`dashboard/spot.py`)

The existing "Dashboard temporarily offline" warning banner was checking whether the server process was alive (`/health`). This always said "OK" even when the server was still starting up and had no data ready. Changed it to check `/health/ready`, which only says "OK" after the data cache has been fully populated. This means the warning banner now stays visible until the server is genuinely ready to show data.

## Why This Fix

The blank-page problem cannot be solved at the CloudFront level because its error-page feature applies globally — there is no way to return HTML for page visits and JSON for data requests. The fix must happen inside the dashboard itself.

The recovery script approach was chosen because:
- It handles all causes of crashes (server replacement, database timeout, network issues), not just one
- It provides clear user feedback ("Reconnecting...") instead of a blank screen
- It recovers automatically — no manual refresh needed
- It is completely independent of the charting library, so it keeps working even when the charts crash

## How the Fix Solves the Problem

**Before:** Server goes down during a visit, data requests receive HTML instead of JSON, charting library crashes, visitor sees a blank screen with no explanation and no recovery path.

**After:** Same scenario, but now:
1. The recovery script detects the crash within 1 second
2. A "Reconnecting..." overlay appears with a spinner and status updates
3. The script polls the server every 5 seconds
4. When the new server is ready, the page reloads automatically with full data

For the database connection issue: stale connections are now detected and replaced before they can cause a visible error, and unreachable databases fail fast (10 seconds) instead of hanging indefinitely.

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/assets/recovery.js` | New file — standalone crash detection and recovery overlay |
| `dashboard/db.py` | Added connection health checking and 10-second timeout to database engine |
| `dashboard/spot.py` | Changed offline banner to check `/health/ready` instead of `/health` |
