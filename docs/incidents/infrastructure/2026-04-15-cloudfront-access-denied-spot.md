# CloudFront "Access Denied" XML During Server Replacement — April 15, 2026

**Date:** 2026-04-15
**Severity:** High — visitors saw a raw error message instead of the loading page during server switches
**Affected components:** CloudFront URL (`https://d17husnpvzzqit.cloudfront.net/dashboard/`), all dashboard paths (`/dashboard/`, `/weather/`, etc.)

---

## What Happened

Roughly one minute after the dashboard stopped responding to interactions, the browser tab switched from showing the dashboard to showing this raw error message:

```
This XML file does not appear to have any style information associated with it.
The document tree is shown below.

<Error>
  <Code>AccessDenied</Code>
  <Message>Access Denied</Message>
</Error>
```

This is not a normal browser error page — it is raw output from Amazon's file storage service (S3), displayed directly in the browser because nothing was there to intercept it.

---

## Why It Happened

### Background: how the dashboard stays online during server switches

The dashboard runs on a discounted "spot" server from Amazon. When Amazon needs the server back, the system automatically starts a replacement — but there is a gap of about 1–3 minutes where the original server is gone and the new one is not ready yet.

To handle this gracefully, CloudFront (Amazon's global content delivery network, which sits in front of the server) is configured with a fallback plan:

1. **Try the live server (EC2)** — if it responds normally, serve the dashboard.
2. **If the server is down**, switch to a backup storage location (S3) that serves a static "Switching Servers" loading page.
3. **If CloudFront encounters a 502/503/504 error** (server unreachable), show the loading page.

This failover system works. The problem was a gap in how errors are handled at step 2.

### The bug: S3 returns "Access Denied" instead of "Not Found"

When the server is down and a visitor requests `/dashboard/`, CloudFront correctly fails over to S3. S3 then looks for a file stored at the path `/dashboard/` — but that file does not exist. S3 only stores one file: the loading page at `/index.html`.

Normally, when a file doesn't exist, you'd expect a "Not Found" (404) error. However, AWS S3 behaves differently for security reasons: **if you don't have permission to list what files exist in a bucket, S3 returns "Access Denied" (403) instead of "Not Found"**. This prevents people from guessing what files are stored in a bucket.

The S3 bucket is intentionally configured this way — listing permissions are not granted because visitors should never need to browse the file list. This is correct security practice. The oversight was that CloudFront was not told what to do when it receives a 403 from S3.

The CloudFront configuration had handlers for 502, 503, and 504 errors — all meaning "the live server failed." But **there was no handler for 403**, so CloudFront passed the raw S3 error XML straight to the visitor's browser.

### Why it showed up "about 1 minute" after the page stopped working

The dashboard polls the server every 5 seconds (to check for spot termination warnings) and every 15 seconds (to check if the server is offline). When the server goes down, these polls start failing and the page becomes non-interactive.

The loading page has a built-in 10-second auto-refresh. After this refresh fired (or after the visitor manually refreshed), the browser made a fresh request to `/dashboard/`. That request hit the 403 bug and displayed the XML error.

---

## The Fixes

### Fix 1 — Added the missing CloudFront error handler for 403 (`terraform/cloudfront.tf`)

Added a handler that intercepts the "Access Denied" error from S3 and serves the "Switching Servers" loading page instead:

| Error code | Meaning | Before | After |
|------------|---------|--------|-------|
| **403** | S3 says "Access Denied" (path not in bucket) | Raw XML shown to visitor | Loading page shown instead |

The handler also sets a 5-second cache window so that as soon as the live server comes back, the next browser refresh serves the real dashboard rather than a cached loading page.

### Fix 2 — Replaced Flask's plain-text 404 response with a helpful HTML page (`dashboard/routes.py`)

Previously, visiting a URL that doesn't exist (e.g. `/nonexistent/`) returned a raw JSON message:
```json
{"error": "Not found"}
```

That's useful for programmatic API clients, but not for a browser visitor who may have mistyped the URL. Replaced it with a styled HTML page that matches the dashboard's dark theme and shows direct links to both dashboards.

**Note:** this is separate from the 403/loading-page fix above. The 403 error only appears when the server is down; a 404 only appears when a visitor visits a URL that genuinely doesn't exist while the server is running normally.

---

## Files Changed

| File | What changed |
|------|--------------|
| `terraform/cloudfront.tf` | Added `custom_error_response` block for 403 — serves loading page during server replacement |
| `dashboard/routes.py` | 404 handler now returns a styled HTML page with links to both dashboards |

---

## How to Deploy

Two changes, two deploy commands:

```bash
# 1. Apply the CloudFront infrastructure change
./scripts/deploy.sh --provision
# Wait 1–5 minutes after terraform finishes for CloudFront to propagate globally

# 2. Deploy the Flask code change (routes.py)
./scripts/deploy.sh
```

---

## How to Verify After Deploying

### Test 1 — Loading screen appears during server replacement (not "Access Denied" XML)

This verifies the core bug fix. It requires taking the server offline briefly.

1. SSH to EC2 and stop K3s (the software that runs the dashboard pod):
   ```bash
   sudo systemctl stop k3s
   ```
2. Wait 5–10 seconds for CloudFront to detect the server is unreachable.
3. In a browser, visit `https://d17husnpvzzqit.cloudfront.net/dashboard/`
4. **Expected result:** the dark "Switching Servers" loading page with a spinner.
   **Before this fix:** you would have seen raw "Access Denied" XML instead.
5. Restore the server immediately:
   ```bash
   sudo systemctl start k3s
   ```
   K3s re-launches all pods automatically; the dashboard is back within ~60 seconds.

### Test 2 — 404 page appears for non-existent URLs (not raw JSON or XML)

This verifies the 404 improvement. No downtime needed — run it while the server is up.

1. Visit any URL that doesn't exist on the dashboard:
   ```
   https://d17husnpvzzqit.cloudfront.net/this-does-not-exist/
   ```
2. **Expected result:** a styled dark "Page not found" page with two buttons linking to the Stocks Dashboard and the Weather Dashboard.
   **Before this fix:** you would have seen a raw `{"error": "Not found"}` JSON message.

---

## What to Watch For in Future

- **Raw XML "Access Denied" in browser during server maintenance:** The 403 handler is now in place. If this recurs, check whether the `custom_error_response` for 403 is still present in `terraform/cloudfront.tf`.
- **Loading page appearing when a visitor simply mistyped the URL:** This should no longer happen — 404s from the live server are handled by Flask's HTML error page, not the loading page.
