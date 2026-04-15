# Weather Dropdown Investigation & Server-Offline Banner

**Date:** April 15, 2026
**Dashboards affected:** Weather (`/weather/`)

---

## What Was Reported

On the Weather page, the "7-Day Hourly Temperature" chart appeared to only show data for New York. When a different city was selected from the dropdown menu at the top of the page, the chart did not update.

---

## Root Cause

The issue was **server downtime**, not a bug in the code.

The dropdown menu works by sending a request to the server each time you make a selection. The server processes the request, looks up the right city's data, and sends back an updated chart. If the server is not running, that request never gets a reply — so the chart simply stays frozen on whatever it was last showing (New York, the default city when the page first loads).

Once the server came back online, the dropdown worked correctly on the first try.

---

## Why This Looked Like a Bug

From a visitor's perspective, the experience was identical to a broken dropdown:

- The page loaded and showed New York's temperature (the default).
- Clicking a different city appeared to do nothing.
- There was no message anywhere on the screen explaining that the server was offline.

In reality, the dropdown was fine. There was just no way for a visitor to know the server was the problem.

---

## What Was Fixed

A new **"Server Offline" banner** was added to both the Stocks and Weather pages. It appears automatically at the top of the page whenever the server becomes unreachable, and disappears on its own once the server recovers.

### What it looks like

A narrow amber-coloured bar appears across the very top of the page with the message:

> **Dashboard temporarily offline**
> The server is not responding — your data may be stale. Please refresh this page in a few minutes.

It uses the same dark amber colour scheme as the existing "Heads up — switching servers" notice, so it fits naturally with the rest of the design.

### How it works (non-technical)

Every 15 seconds, a small piece of code built into the page silently pings the server with a quick "are you there?" check. This check runs entirely inside the visitor's browser — it does not need the server to be up in order to run. If the server answers, nothing changes. If the server does not answer, the banner immediately appears. The moment the server comes back online, the next check succeeds and the banner disappears without the visitor needing to do anything.

---

## Technical Summary (for developers)

| File | Change |
|------|--------|
| `dashboard/spot.py` | Added `build_offline_layout_components(prefix)` (returns a `dcc.Interval` + hidden banner `html.Div`) and `register_offline_callbacks(dash_app, prefix)` (registers a `clientside_callback` that `fetch()`-es `/health` every 15 s and sets the banner's `style` to `display:flex` on failure or `display:none` on success). Clientside callback chosen deliberately — it runs in the browser even when the Flask server is down. |
| `dashboard/app.py` | Updated import to include the two new helpers; added `*build_offline_layout_components("stocks")` and `*build_offline_layout_components("weather")` to both Dash layouts; added `register_offline_callbacks()` calls after the existing spot-callback registrations. |
| `dashboard/assets/theme.css` | Added `.offline-banner`, `.offline-banner__icon`, `.offline-banner__body`, `.offline-banner__title`, and `.offline-banner__message` — full-width fixed bar at `top:0`, dark amber background, `z-index:9999`. |

**Why a clientside (JavaScript) callback instead of a regular Python callback:**
Regular Dash callbacks make a network request to the Flask server to run. If the server is down, those requests fail silently — the callback simply never fires. A clientside callback is JavaScript code that ships to the browser when the page first loads. Once in the browser, it runs on its own timer and can call `fetch('/health')` regardless of whether the server is currently reachable. A network error on that fetch is caught by the `.catch()` handler, which is exactly what triggers the banner.

**Interval cadence — 15 seconds:**
Fast enough that a visitor sees the banner quickly after a server restart or crash. Slow enough that it adds no meaningful load to the server when it is running normally.
