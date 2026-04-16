# False "Dashboard Offline" Banner on Every Page Load

**Date:** 2026-04-15
**Severity:** Medium — every page visit triggers a misleading warning banner and a browser console error, even when the dashboard is working correctly

## What Happened

After the earlier fixes for spot instance recovery (see `2026-04-15-dashboard-blank-page-spot-replacement.md` and `2026-04-15-recovery-js-syntax-error.md`), two cosmetic issues remained every time a user opened the dashboard:

1. **A console error appeared immediately:** `Unhandled Promise Rejection: SyntaxError: The string did not match the expected pattern.` — this was logged in the browser's developer tools on every page load.

2. **The "Dashboard temporarily offline" banner appeared** — the ⚠ banner saying "The server is not responding — your data may be stale" showed up at the top of the page, even though the charts loaded and everything worked correctly moments later.

Both issues resolved themselves after about 15 seconds: the error was a one-time event, and the banner disappeared on the next automatic health check. But the experience was alarming — a visitor opening the dashboard for the first time would see a warning saying the server was offline while the graphs were actively loading.

## Root Cause

### Why the "offline" banner appeared (the main bug)

The dashboard has a background health check that runs every 15 seconds. It asks the server: "Are you ready?" When the answer is "not yet," the banner is supposed to appear.

The server has two different states:
- **Starting up** — the server process is running and the page loads, but the data is still being pre-loaded into memory. The server responds with a "503 warming up" status code.
- **Truly offline** — the server process is completely unreachable. The browser cannot connect at all.

The banner logic treated both situations identically: anything other than a "200 OK" response showed the banner. So when a user first opened the page while the server was still warming up its data cache (which takes 30–60 seconds), the very first health check got a "503 warming" response and triggered the "server not responding" banner — even though the server was responding perfectly fine, just loading data in the background.

### Why the console error appeared

When the CloudFront loading page redirects the user to the dashboard, there is a brief moment where the dashboard's internal machinery starts up and makes background requests for its layout data. If one of those requests arrives just as the server is finishing the redirect, it can receive an HTML page instead of the structured data it expected, causing this one-time error. The dashboard recovers immediately and retries the request, which is why graphs load correctly — but the error is still logged.

The crash-recovery script (`recovery.js`) was already designed to catch this exact error. However, it was written to only act on errors that occur *after* the page has fully rendered its first layout, on the assumption that pre-render errors were too early to act on. So it saw the error, decided it was too early to intervene, and did nothing — leaving the console message without any visible response.

## What Was Fixed

### 1. Smarter "offline" detection (`dashboard/spot.py`)

The health check's browser-side logic was updated to distinguish between the two server states:

| Server response | Old behavior | New behavior |
|---|---|---|
| 200 OK — ready | Hide banner | Hide banner (unchanged) |
| 503 — still warming up | **Show banner** ← bug | **Hide banner** ← fix |
| Other error (502, 504, etc.) | Show banner | Show banner (unchanged) |
| No response at all | Show banner | Show banner (unchanged) |

The "warming up" state is now correctly treated as "not offline" — the server is running, it just hasn't finished pre-loading its data yet. The "Reconnecting..." overlay (from `recovery.js`) handles user communication during spot replacements, so the offline banner is reserved for genuine, unexpected server problems.

### 2. Smarter crash detection for early errors (`dashboard/assets/recovery.js`)

The crash-recovery script was updated to handle errors that occur before the page finishes rendering its first layout. Previously, any such error was silently ignored. Now:

- If a crash-type error fires before the page has rendered, a 5-second countdown starts.
- If the page renders successfully within those 5 seconds (meaning the error was a harmless one-time hiccup during the redirect), the countdown is cancelled — no overlay, no disruption.
- If the page has still not rendered after 5 seconds (meaning the error genuinely prevented the dashboard from loading), the "Reconnecting..." overlay appears and begins polling the server.

This means harmless startup errors during CloudFront redirects no longer trigger the overlay, while genuine loading failures are still caught and handled.

## Result

After these fixes, opening the dashboard for the first time produces a clean experience:

- No "Dashboard temporarily offline" banner during normal startup
- No console errors from the CloudFront redirect sequence
- The "Reconnecting..." overlay still appears correctly when the server genuinely goes down (spot replacement)
- The offline banner still appears correctly when the server returns an unexpected error

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/spot.py` | Health check now treats HTTP 503 (warming up) as "hide banner" instead of "show banner" |
| `dashboard/assets/recovery.js` | Added 5-second timeout for pre-render errors so harmless startup hiccups don't trigger the overlay |
| `docs/incidents/dashboard/2026-04-15-offline-banner-on-startup.md` | This document |
