# "Reconnecting" Overlay Not Appearing During Server Replacement

**Date:** 2026-04-15
**Severity:** Medium — the dashboard shows errors or a frozen spinner instead of the "Reconnecting…" overlay during and after a spot instance replacement; manual refresh is required

---

## What Happened

Two separate failure modes were observed during spot instance replacement testing:

**On page open (startup failure):**
The browser console showed this error three times immediately after opening the dashboard:
```
Unhandled Promise Rejection: SyntaxError: The string did not match the expected pattern.
```
The dashboard did not show the "Reconnecting…" overlay. A manual page refresh was required to get a working view.

**While the page was already open (mid-session failure):**
After leaving the dashboard open, the console showed:
```
Failed to load resource: The network connection was lost. (_dash-update-component, line 0)
Error: Callback failed: the server did not respond.
```
Again, no overlay appeared. The dashboard was stuck showing error states inside each chart.

---

## Root Cause

### Startup failure — two bugs in the same timer

The recovery script (`recovery.js`) was designed to detect the startup `SyntaxError` and start a 5-second countdown. If the dashboard hadn't loaded by the time the countdown finished, the overlay would appear.

This approach had two independent bugs that together guaranteed the overlay would never show:

**Bug 1 — Timer cancelled too early.**
Dash renders a loading skeleton almost instantly (a fraction of a second), even before it has successfully received any data from the server. When that skeleton appeared, the script saw "the container now has content" and cancelled the timer — interpreting it as a successful load. In reality, the skeleton only meant React had started up; the actual layout data had not arrived yet (and was failing with a `SyntaxError`).

**Bug 2 — Timer condition was already false when it fired.**
Even if the timer had not been cancelled, its check was: "if the page still hasn't rendered, show the overlay." Because the skeleton had already appeared, this condition was `false` the moment the timer fired — so the overlay would have been skipped regardless.

Both bugs had to be present for the overlay to fail; fixing either one alone would not have been enough.

**Additionally:** The `instanceof SyntaxError` check used to detect the error can fail in some browsers when the error originates in a different execution context. A fallback using `.name === "SyntaxError"` was missing.

### Mid-session failure — error type not recognised

When the server goes offline while the page is already open, Dash reports the failure as:
```
Error: Callback failed: the server did not respond.
```

The recovery script's error detector only watched for two things: Redux errors and `SyntaxError` (JSON parse failures). The "Callback failed" error is a completely different type and was never added to the watch list. Because no recognised error was seen, the script did nothing.

Additionally, when Dash's callbacks fail mid-session, it shows error indicators *inside* the existing chart components — it does not clear the page container. The script's secondary detection method (watching for the container to be emptied) therefore never triggered either.

There was no fallback mechanism to notice mid-session server loss.

---

## What Was Fixed

All changes are in **`dashboard/assets/recovery.js`**.

### Fix 1 — Replace the broken 5-second timer with a health check

Instead of "wait 5 seconds and check if the page rendered," the script now does:

> "Wait 3 seconds (to allow Dash to retry on its own), then ask the server `/health/ready`."

| Server response | Action |
|---|---|
| `200 OK` — server is healthy | Reload the page immediately (error was transient) |
| `503` or other error | Show "Reconnecting…" overlay (server is still starting up) |
| No response at all | Show "Reconnecting…" overlay (server is unreachable) |

This completely removes the dependency on whether Dash's container has children, avoiding both bugs above.

### Fix 2 — Add "Callback failed" to the error watch list

The script now also triggers when it sees the "Callback failed: the server did not respond" message, which is what Dash reports when it cannot reach `/_dash-update-component`. After the same 3-second health check, the correct action is taken.

### Fix 3 — Add a periodic health monitor for mid-session server loss

Even when no JavaScript error reaches the recovery script, the new health monitor provides a safety net:

- Starts running once the dashboard has fully rendered its first layout
- Checks `/health/ready` every 10 seconds
- If the server becomes unreachable (network failure) or returns an unexpected error code, it shows the "Reconnecting…" overlay

This means that even if Dash handles the "server gone" error internally and logs it without firing a detectable window event, the overlay will appear within 10 seconds regardless.

### Fix 4 — More robust SyntaxError detection

The error check now uses `.name === "SyntaxError"` as a fallback alongside `instanceof SyntaxError`, which is more reliable across browsers and execution contexts.

---

## Result

| Scenario | Before | After |
|---|---|---|
| Page opened while server is warming up | Console errors, blank/frozen page, manual refresh needed | Overlay appears within 3 s, auto-reloads when ready |
| Page opened on a healthy server | Normal load | Normal load (unchanged) |
| Server replaced while page is open | Frozen charts, no overlay | Overlay appears within 10 s, auto-reloads when ready |
| Server back online | User must manually refresh | Page reloads automatically |

---

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/assets/recovery.js` | Fixed broken timer logic; added `isCallbackFail` detection; added `.name` fallback; added `_startHealthMonitor()` for mid-session detection |
| `docs/incidents/dashboard/2026-04-15-recovery-js-overlay-not-showing.md` | This document |
