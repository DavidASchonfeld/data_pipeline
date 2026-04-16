# "Callback failed: the server did not respond" — Weather Page Navigation During Server Switch

**Date:** 2026-04-15
**Severity:** Medium — when a spot server switch happens while the user navigates to the weather page, the dashboard shows broken charts for up to 10 seconds with no explanation and no "Reconnecting" overlay
**Status:** Fixed

---

## What Happened

A user clicked the "View Weather Dashboard →" link on the stocks page. The browser loaded the weather page, but the charts and tables never appeared. The browser's developer console showed:

```
[Error] Error: Callback failed: the server did not respond.
```

The "Reconnecting…" overlay — which is supposed to appear whenever the server is unavailable — did not show up. The page stayed broken and silent, with no message telling the user what was happening or that it would recover automatically.

---

## Root Cause

### How the dashboard and server talk to each other

When you visit the weather page, the browser first downloads the page itself (HTML, styles, scripts). Then Dash — the charting framework the dashboard is built on — makes several follow-up requests in the background to fetch the actual data for each chart and table.

During a spot server switch, there is a brief window where the old server is shutting down and the new server is still warming up. If the user navigates to the weather page during that window:

1. The page HTML loads successfully (the old server may still serve static content for a moment).
2. Dash fires its data requests — but the old server is now gone.
3. The requests fail. Dash detects this, generates the error message, and **catches it internally**.

### Why the overlay never appeared

The recovery script (`recovery.js`) is designed to detect exactly this kind of failure and show the "Reconnecting…" overlay. It listens for uncaught JavaScript errors via the browser's standard error event system.

The key word is **uncaught**. When Dash detects a failed data request, it handles the error itself — it logs the message to the browser console using `console.error()` and then moves on. Because Dash caught the error internally, the browser never raises it as an unhandled event. The recovery script's listener was waiting for an event that never arrived.

The recovery script does have a backup mechanism — a health check that runs every 10 seconds regardless of errors. This would eventually show the overlay, but it could take up to 10 seconds. During that window, the user sees a broken page with no explanation.

### Why navigating between dashboards made this more visible

The stocks and weather dashboards are two separate applications on the same server. Navigating between them is a full page reload, not a smooth in-app transition. When the weather page loads fresh, it fires all of its data requests simultaneously. If any of those requests fail, the error appears right away — there is no graceful fallback or staggered loading to cushion the impact.

---

## What Was Fixed

### 1. Immediate error detection via `console.error` interception (`recovery.js`)

A new layer was added to the recovery script that intercepts `console.error()` calls made anywhere on the page. When it sees the specific message Dash uses for failed data requests — "Callback failed" or "server did not respond" — it immediately starts the recovery flow:

1. Wait 3 seconds (gives Dash a chance to retry on its own).
2. Ask the server: are you healthy?
   - **Yes** → reload the page (the error was a brief blip; reload gives the user a clean view).
   - **No / unreachable** → show the "Reconnecting…" overlay and keep checking every 5 seconds.

This brings the overlay response time from up to 10 seconds down to about 3 seconds, and makes it independent of how Dash reports the error internally.

### 2. Readiness check before routing traffic to the new server (`pod-flask.yaml`)

The system that manages the server (Kubernetes) continuously checks whether the server is healthy before sending users to it. Previously, the check used a simple "is the server process running?" question (`/health`). This said "yes" as soon as the process started, even if the server hadn't finished loading its data yet.

Changed to a stricter check (`/health/ready`) that only says "yes" after the server has fully loaded all data into memory. This prevents a window where the new server accepts requests but has nothing to serve, which would cause the same "Callback failed" error on the fresh server.

### 3. Graceful shutdown before the old server disappears (`gunicorn.conf.py` + `pod-flask.yaml`)

When AWS reclaims the spot server, the system sends a shutdown signal. Without an explicit grace period, the server process could be cut off while still handling an in-flight request — which looks identical from the user's perspective to the server going away mid-request.

Two settings now work together:

- **`terminationGracePeriodSeconds: 60`** — Kubernetes waits up to 60 seconds for the server to finish shutting down cleanly before it forcibly ends the process.
- **`graceful_timeout = 30`** — Gunicorn (the server process manager) finishes any requests currently being handled (up to 30 seconds) before stopping. This value is deliberately smaller than the 60-second Kubernetes limit so the process always has time to exit cleanly.

---

## Result

| Scenario | Before | After |
|---|---|---|
| User navigates to weather page during server switch | Broken charts for up to 10 s, no overlay | "Reconnecting…" overlay appears within ~3 s |
| New server accepting requests before data is loaded | Possible | Prevented by the stricter readiness check |
| In-flight requests cut off when old server shuts down | Possible (no grace period) | Protected: 30 s drain window before shutdown |

---

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/assets/recovery.js` | Added `console.error` interceptor so Dash's internally caught "Callback failed" message triggers the recovery overlay immediately |
| `dashboard/manifests/pod-flask.yaml` | Readiness probe changed from `/health` to `/health/ready`; added `terminationGracePeriodSeconds: 60` |
| `dashboard/gunicorn.conf.py` | Added explicit `graceful_timeout = 30`; updated docstring to document the relationship with the pod's termination grace period |
