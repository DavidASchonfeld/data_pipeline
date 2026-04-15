# CloudFront Dashboard Showing Empty Graphs + Console Error — April 15, 2026

**Date:** 2026-04-15
**Severity:** High — dashboard was publicly reachable but showed no data for all users
**Affected components:** CloudFront URL (`https://d17husnpvzzqit.cloudfront.net/dashboard/`), weather page (`/weather/`)

---

## What Happened

The dashboard at the CloudFront URL loaded the page shell (title, buttons, dropdowns) but every chart and table showed up empty — no stock data, no anomaly scores, no weather readings. Opening the browser's developer console revealed a JavaScript error:

```
[Error] Unhandled Promise Rejection: SyntaxError: The string did not match the expected pattern.
```

The direct EC2 URL (`http://52.70.211.1:32147/dashboard/`) worked perfectly and showed all data. Only the public CloudFront URL was affected.

---

## Why It Happened

### Problem 1 — Charts receive no data (the core issue)

When you visit the dashboard, the browser loads the page layout and then makes a series of background requests to the server to fetch the actual chart data. These requests use a method called **POST** (used for sending data to a server, not just loading a page). Dash, the library that powers the charts, uses POST internally to ask the server "give me the data for this chart."

CloudFront sits in front of EC2 and decides which requests to allow through. It was configured with a rule:

> "For requests to paths starting with `/_dash-*`, allow POST."

The problem is that the dashboard's charts live at `/dashboard/` as their base address — so the actual data request path is `/dashboard/_dash-update-component`, not `/_dash-update-component`. The rule `/_dash-*` only matched the shorter form, so every chart data request fell through to the default rule, which **only allows GET requests** (the browser equivalent of "just loading a page"). All POST requests — every chart, every table — were silently rejected.

The browser received an error response but Dash expected a data payload, causing the SyntaxError and leaving every chart blank.

### Problem 2 — Cold Snowflake timeouts could cause the same symptom

The dashboard queries Snowflake (the data warehouse) the first time it starts up after being idle. Snowflake's computing cluster can take 30–60 seconds to "wake up" after inactivity. CloudFront was configured to wait only **10 seconds** for EC2 to respond before giving up and returning an error. If CloudFront timed out during a Snowflake cold start, it would serve the "switching servers" loading page HTML instead of the expected chart data — causing the same SyntaxError in Dash's JavaScript.

### Problem 3 — Rate limiting treated all users as one

The dashboard has a built-in limit of 100 requests per minute per IP address to prevent abuse. Normally this works per-person, but when traffic goes through CloudFront, all requests arrive at EC2 from the same CloudFront edge server IP address — not each user's real home IP. This meant all users were sharing one 100/min bucket. On a cold page load, Dash fires up to 7 background requests at once. With spot polling (every 5 seconds), this adds up quickly. If the shared bucket ran out, EC2 returned a rate-limit error as an HTML page — and Dash's JavaScript, which expected JSON, threw the same SyntaxError.

---

## The Fixes

### Fix 1 — Corrected the CloudFront path pattern (`terraform/cloudfront.tf`)

Changed one character in the path pattern rule:

| Before | After |
|--------|-------|
| `/_dash-*` | `/*_dash-*` |

The `*` at the start means "match any prefix, including `/dashboard/` or `/weather/`." This ensures POST requests for chart data are always forwarded to EC2, regardless of which dashboard page they come from.

### Fix 2 — Increased CloudFront's patience for slow responses (`terraform/cloudfront.tf`)

Increased `origin_read_timeout` from 10 seconds to 60 seconds. CloudFront will now wait up to a full minute for EC2 to reply before giving up. This matches the time a cold Snowflake warehouse can take to activate, so users on the first request after the system has been idle will see a brief wait rather than an error.

### Fix 3 — Rate limiting now tracks real users, not CloudFront's IP (`dashboard/security.py`)

CloudFront adds a special hidden header to every request it forwards: `CloudFront-Viewer-Address`, which contains the actual user's real IP address. The rate limiter was updated to read this header instead of the server connection's IP. Each user now has their own independent 100-requests/minute counter instead of sharing one counter with everyone else.

### Fix 4 — Rate limit errors now return the right format (`dashboard/routes.py`)

When the rate limit is hit, the server previously returned a plain HTML error page. Dash's JavaScript was not prepared for HTML and threw a SyntaxError when it tried to read the response. Added a handler that returns a clean JSON error message instead:

```json
{"error": "Rate limit exceeded", "message": "Too many requests — please try again later."}
```

Dash can read this without crashing.

---

## Files Changed

| File | What changed |
|------|--------------|
| `terraform/cloudfront.tf` | Fixed path pattern `/_dash-*` → `/*_dash-*`; increased `origin_read_timeout` 10s → 60s |
| `dashboard/security.py` | Rate limiter now uses `CloudFront-Viewer-Address` header for per-user limits |
| `dashboard/routes.py` | Added JSON error handler for 429 (rate limit exceeded) responses |

---

## How to Deploy

```bash
# Apply CloudFront infrastructure changes (takes ~1-2 min, then 5-10 min to propagate globally)
echo "yes" | ./scripts/deploy/terraform.sh apply

# Deploy Flask code changes (security.py + routes.py)
./scripts/deploy.sh
```

---

## How to Verify After Deploying

1. Wait 5–10 minutes after `terraform apply` completes for CloudFront to finish rolling out the change globally
2. Open `https://d17husnpvzzqit.cloudfront.net/dashboard/` in a browser
3. All stock charts and the anomaly table should populate with data (same as the direct EC2 URL)
4. Repeat for `https://d17husnpvzzqit.cloudfront.net/weather/` — weather charts should also load
5. Open the browser's developer tools → Network tab → look for `_dash-update-component` requests — they should return `200 OK` with JSON data, not errors

---

## What to Watch For in Future

- **Charts blank on CloudFront but fine on EC2:** Check the Network tab in browser developer tools. If `_dash-update-component` returns `403` or `405`, a CloudFront path rule is blocking POST requests again.
- **Charts blank only on first visit after a long idle period:** Likely a Snowflake cold-start timeout. The 60s timeout should cover this, but if the warehouse activation gets slower, the timeout may need increasing again in `cloudfront.tf`.
- **Rate limit errors appearing in browser console:** Check `security.py` — the `CloudFront-Viewer-Address` header parsing may need updating if CloudFront's header format changes.
