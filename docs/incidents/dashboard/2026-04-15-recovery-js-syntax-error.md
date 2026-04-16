# Recovery Script Not Triggering During Server Replacement

**Date:** 2026-04-15
**Severity:** Medium — dashboard graphs hang indefinitely during a spot instance replacement instead of showing the "Reconnecting" overlay

## What Happened

After fixing the blank-page crash caused by server replacements (see `2026-04-15-dashboard-blank-page-spot-replacement.md`), the recovery overlay was still not appearing. Visiting the dashboard during or just after a spot instance replacement showed graphs stuck in a permanent loading spinner. Switching to a different page and back (which forces a full reload) made everything work immediately, confirming the issue was the old session getting stuck rather than any data problem.

The browser developer console showed these errors repeated four times:

```
Unhandled Promise Rejection: SyntaxError: The string did not match the expected pattern.
```

## Root Cause

The recovery script (`recovery.js`) was written to detect two specific types of crashes that leave the page blank. It looked for error messages containing the words "Minified Redux error" or "redux".

The error the dashboard actually throws in this scenario is a `SyntaxError` — a different type of error with a completely different message. This happens because:

1. The dashboard receives HTML (the "Switching servers" page from CloudFront) where it expected structured data (JSON)
2. The dashboard tries to read this HTML as data and fails
3. This specific failure produces a `SyntaxError`, not a "Redux error"

Because the recovery script was only watching for Redux errors, it saw the `SyntaxError`, didn't recognise it as a crash worth recovering from, and did nothing. The "Reconnecting" overlay never appeared.

The difference between the two error types:

| Error type | When it happens | Recovery script detected it? |
|---|---|---|
| "Minified Redux error" | React's rendering engine crashes | ✅ Yes (already working) |
| `SyntaxError` (JSON parse) | Dashboard receives HTML instead of data | ❌ No (this fix) |

Both errors are caused by the same underlying event (server replacement), but produce different error types depending on which part of the dashboard encounters the bad HTML first.

## What Was Fixed

**`dashboard/assets/recovery.js`** — added a second detection condition alongside the existing Redux check.

The fix teaches the recovery script to also trigger when it sees an unhandled `SyntaxError` inside a Promise. This is a precise match: synchronous JavaScript errors (normal bugs) produce a different type of event and are not affected. Only failed data-fetch operations — exactly what happens when the server returns HTML instead of JSON — produce the pattern this fix catches.

```
Before: only watched for "Minified Redux error" / "redux" in the error message
After:  also watches for SyntaxError thrown inside a Promise (response.json() failure)
```

## Result

With this fix in place, the full recovery flow now works correctly for both error types:

1. Server replacement begins → CloudFront starts serving the "Switching servers" HTML page
2. Dashboard data requests receive HTML instead of JSON → `SyntaxError` thrown
3. Recovery script detects the `SyntaxError` → shows "Reconnecting…" overlay immediately
4. Script polls `/health/ready` every 5 seconds in the background
5. New server finishes starting up and data cache warms → `/health/ready` returns OK
6. Page reloads automatically with fresh data

Visitors now see the "Reconnecting…" overlay instead of a frozen spinner, and the page recovers without any manual action.

## Files Changed

| File | What changed |
|------|--------------|
| `dashboard/assets/recovery.js` | Added `SyntaxError` detection to the crash recovery handler |
| `docs/incidents/dashboard/2026-04-15-recovery-js-syntax-error.md` | This document |
