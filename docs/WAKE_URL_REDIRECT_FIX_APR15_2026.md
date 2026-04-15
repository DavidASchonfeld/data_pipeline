# Dashboard Wake-Up: Connection Error Fix (April 15, 2026)

## What Happened

When visiting the dashboard in the morning after it had been idle overnight, the browser
showed:

> "Safari can't open the page because the server unexpectedly dropped the connection."

The URL being visited was the raw server address (`http://52.70.211.1:32147/dashboard/`).

---

## Why It Happened

The dashboard runs on a server that shuts itself down when nobody has used it for 45
minutes. This saves roughly $10–60 per month compared to leaving the server running
around the clock.

When the server is off, it truly does not exist — there is nothing there to answer a
connection. Any attempt to reach the raw server address fails immediately with the error
above.

The system has a dedicated "wake-up link" (the API Gateway URL, available via
`terraform output dashboard_url`) that handles this correctly: visiting it shows a
loading screen while the server boots, then automatically forwards you to the live
dashboard once it is ready. That link works whether the server is on or off.

**The problem was a bookmark.** After a successful wake-up, the system was forwarding
visitors directly to the raw server address via a browser redirect. The browser recorded
that raw address in its history and address bar. Visitors naturally bookmarked or
returned to that address — not knowing it only works while the server is running.

---

## What Was Fixed

The wake-up system (`terraform/lambda/wake.py`) previously sent visitors to the live
server using a silent browser redirect — the kind that changes the address bar immediately
with no visible pause.

It now shows a brief "Dashboard is ready" screen for about 2.5 seconds **before**
forwarding visitors onward. Crucially, this screen appears while the visitor is still
on the wake-up link (the correct URL to save), not yet on the raw server address.

The screen shows:
- A clear "Save this link" notice
- The exact URL currently in the address bar (the wake-up link)
- A button to open and bookmark that URL

After 2.5 seconds, the browser is forwarded to the live dashboard automatically.

**No infrastructure changes were needed.** The fix is entirely in the wake-up function
that decides what to show visitors when the server is ready.

---

## How to Confirm It Is Working

1. Get the wake-up link by running `terraform output dashboard_url` in the project
   directory.
2. Let the server go to sleep (or put it to sleep with `./scripts/deploy.sh --sleep`).
3. Visit the wake-up link. Wait through the loading screen (~3–5 minutes).
4. When the server is ready, you should see a green "Dashboard is ready!" card for about
   2.5 seconds — with the wake-up link displayed and a bookmark button.
5. The browser then forwards you to the live dashboard automatically.

Going forward, the link to share and bookmark is the wake-up link from step 1 — not the
address that ends up in the address bar once the dashboard loads.

---

## Root Cause Summary

| | Before | After |
|---|---|---|
| When server is ready | Silent 302 redirect to raw server IP | Bridge page shown first (on the correct URL), then forward to server |
| User's address bar after visit | Raw server IP (`52.70.211.1:32147/...`) | Same — but user has seen the correct URL to bookmark |
| Next visit while server is sleeping | Connection error | Loading screen (if using wake-up link) |
