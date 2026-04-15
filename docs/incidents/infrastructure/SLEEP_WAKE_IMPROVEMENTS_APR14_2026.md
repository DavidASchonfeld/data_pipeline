# Sleep/Wake System — Improvements (April 14, 2026)

> **Historical document (archived 2026-04-15).** The sleep/wake system described here was removed the following day. The server now runs continuously on spot pricing. This document is kept for reference only.

## What Is the Sleep/Wake System?

To keep costs low, the pipeline server does not run continuously. Instead it "sleeps" by
shutting down the server when nobody has used the dashboard for 45 minutes, and "wakes up"
automatically when someone visits the dashboard URL. A full wake-up takes 3–5 minutes if
a saved server image (AMI) is available.

When the server is sleeping, visiting the dashboard URL shows a loading page that refreshes
itself every 10 seconds. Once the server is ready, the browser is automatically sent to the
dashboard.

---

## What Changed in This Update

Three improvements were made to the sleep/wake system.

---

### 1. Automatic Server Snapshots After Every Deploy

**What it does:**
Every time a full deploy finishes successfully, the system now automatically takes a snapshot
of the running server (called a "golden AMI") in the background. This snapshot is used for
the next cold boot so the server starts in 3–5 minutes instead of 60 minutes.

**Why it was added:**
Previously, snapshots had to be taken manually with a separate command (`--bake-ami`). This
meant that if someone deployed new code and then the server went to sleep, the next boot
would either use a stale snapshot (old code) or fall back to a full 60-minute bootstrap.
With auto-baking, the snapshot always reflects the latest successful deploy.

**How it works without slowing down the deploy:**
The snapshot is kicked off at the very end of the deploy script — *after* the "Deploy
Complete" message and server URLs are printed. The actual snapshot creation runs silently
in the background. The deploy script exits immediately so you can use the server right away.
Services on the server briefly restart once (~60 seconds) for a clean snapshot, then
everything goes back to normal. The background job finishes on its own in 15–25 minutes.

You can watch the background bake progress at any time with:
```
tail -f /tmp/ami-bake.log
```

**Safety guards:**
- The bake only runs after all deploy steps pass. A broken deploy will not produce a snapshot.
- If you deploy again before the previous bake finishes (bakes take 15–25 min), the second
  deploy detects the in-progress bake and skips starting a new one, preventing two snapshots
  from running at the same time.
- Any bake that crashes without cleaning up is automatically ignored after 60 minutes so
  it can never block future deploys permanently.

---

### 2. Loading Page Remembers Where You Were Going

**What it does:**
When the server is sleeping and you visit a specific page — for example, the weather
dashboard at `/weather/` — the loading page now remembers that destination. Once the
server is ready, the browser takes you directly to `/weather/` instead of always sending
you to the main `/dashboard/` page.

**Why it was added:**
Previously the loading page always redirected to `/dashboard/` no matter where you
originally tried to go. This was annoying if you had bookmarked or shared a direct link
to a specific section. Now the server honors your original destination.

No changes to the loading page design were needed — the page already refreshes to the
correct URL. The fix was in the wake-up function that decides where to send you when
the server is finally ready.

---

### 3. Old AWS Resources Are Now Cleaned Up Automatically

Two types of AWS resources were found to accumulate silently over time. Both have been
fixed.

#### Safety Snapshots (before Terraform replacements)

When the infrastructure management tool (Terraform) detects that the server needs to be
replaced, it automatically creates a safety snapshot of the server's disk beforehand —
just in case something goes wrong and you need to recover data. This is good practice,
but the old safety snapshots were never deleted. Over time they would pile up and add to
the monthly AWS storage bill (each 30 GB snapshot costs roughly $1.50/month).

**Fix:** After creating a new safety snapshot, the two oldest safety snapshots are now
automatically deleted. The two most recent are kept as a short recovery window.

#### Launch Template Versions

Every time a new server snapshot (AMI) is created, a new "launch template version" is
also created in AWS. These record which snapshot to use for the next server boot. They
are free to store, but they accumulated forever.

**Fix:** After creating a new launch template version, old versions are trimmed so only
the 3 most recent are kept. This provides a short rollback window (you can revert to
a previous snapshot if needed) without unbounded growth.

---

## Full Resource Audit Summary

The table below shows every type of AWS resource this system creates and whether it
can accumulate over time. Items marked "Fixed" were addressed in this update.

| Resource | Accumulates? | Notes |
|---|---|---|
| EC2 server instances | No | Only 1 can ever exist at a time |
| EBS disk volumes | No | Auto-deleted when server shuts down |
| Golden AMI snapshots | No | Old ones deleted automatically each bake |
| AMI backing snapshots | No | Deleted alongside old AMIs |
| Safety snapshots | **Fixed** | Now capped at 2 most recent |
| Launch template versions | **Fixed** | Now capped at 3 most recent |
| Lambda function calls | Negligible | Free-tier pricing; less than $0.01/month |
| Elastic (static) IP address | Intentional | Small charge (~$0.005/hr) when server is off; needed for stable SSH access |
| EventBridge timer calls | Negligible | Free-tier pricing |

---

## How to Use

| Command | What it does |
|---|---|
| `./scripts/deploy.sh` | Full deploy + auto-bakes AMI in background afterward |
| `./scripts/deploy.sh --dags-only` | Fast DAG-only deploy (no auto-bake) |
| `./scripts/deploy.sh --bake-ami` | Manually bake a snapshot right now |
| `./scripts/deploy.sh --wake` | Wake the server without deploying |
| `./scripts/deploy.sh --sleep` | Put the server to sleep immediately |
| `tail -f /tmp/ami-bake.log` | Watch background bake progress |
