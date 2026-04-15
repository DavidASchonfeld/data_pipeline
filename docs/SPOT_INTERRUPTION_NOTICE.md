# Spot Instance Interruption Notice

## The Short Version

When the server powering this dashboard is about to be briefly taken offline by AWS,
a small amber countdown banner automatically appears in the bottom-right corner of every
page. It shows exactly how many minutes and seconds remain. Behind the scenes, a
replacement server starts booting up the moment the warning appears — so by the time
the old server goes offline, the new one is already well on its way. The site comes
back on its own within a couple of minutes, and no action is needed from you.

---

## Why the Server Can Be Taken Offline

This pipeline runs on what AWS calls a **Spot Instance** — spare computing capacity that
AWS rents out at a 60–80% discount compared to standard pricing. The tradeoff is that
AWS can reclaim that capacity at any time if it's needed elsewhere. When that happens,
AWS gives exactly a **two-minute warning** before shutting the server down.

This is a deliberate cost-efficiency choice: running 24/7 on standard ("on-demand")
pricing would cost significantly more for what is essentially a read-only analytics
dashboard. The savings go directly into keeping the pipeline running longer and on more
capable hardware.

After the server goes offline, it automatically restarts within a few minutes — the
underlying infrastructure is designed to recover without any manual intervention.
(See [ON_DEMAND_ARCHITECTURE.md](ON_DEMAND_ARCHITECTURE.md) for details on how that recovery process works.)

---

## How the Warning Banner Is Triggered

AWS posts the shutdown notice to a private internal address that is only readable from
within the server itself. Every five seconds, the dashboard quietly checks that address
in the background.

The moment a notice appears, the banner is shown to everyone currently viewing the site.
No human needs to be watching — the detection and display happen automatically.

---

## What the Banner Looks Like

A small amber-coloured panel appears fixed in the **bottom-right corner** of the screen.
It stays visible for the entire two-minute countdown and does not disappear on its own.

It contains:

- A warning icon (⚠) and the heading **"Heads up — brief maintenance coming"**
- The message: *"This website will go offline temporarily in:"*
- A live **M:SS countdown** (e.g. `1:47`, counting down to `0:00`)
- A note: *"Your data is safe. A replacement server is already starting up."*

The banner does not block the dashboard — you can continue reading charts or tables while
the countdown runs. It is purely informational.

---

## What Happens During Those Two Minutes

The two-minute window is not wasted waiting time. The moment AWS issues the warning,
the infrastructure automatically starts booting a fresh replacement server in the
background. That process uses a pre-saved server snapshot (called an AMI) which already
has all the software installed — think of it like restoring a laptop from a backup
rather than setting it up from scratch.

By the time the old server actually shuts off, the replacement has already had a
two-minute head start. This reduces the time visitors spend on the loading screen from
roughly three to five minutes down to roughly one to three minutes.

Here is what the full sequence looks like from the moment the warning fires:

| Time | What's happening |
|------|-----------------|
| **0:00** | AWS issues the warning. The countdown banner appears. A replacement server starts booting in the background. |
| **0:00 – 2:00** | The old server keeps running normally. You can still use the dashboard. |
| **2:00** | AWS shuts down the old server. The stable public IP address is immediately transferred to the new one. |
| **2:00 – 4:00** | The loading page appears while the replacement server finishes booting. |
| **~3–4 min** | The server is ready. The loading page redirects to the live dashboard automatically. |

---

## What Happens to Your Data

All pipeline data (stock financials, weather readings, anomaly results) is stored in
**Snowflake**, an external cloud database that has nothing to do with this server. When
the server shuts down, Snowflake keeps running. Nothing is lost, overwritten, or
interrupted — the server is only responsible for serving the web pages, not for storing
the data.

The Airflow scheduler (which ingests new data) runs as a separate process on the same
server. Any data ingestion job that was in progress at the moment of shutdown will simply
re-run on the next scheduled cycle after the server restarts.

---

## What You Should Do

**Nothing.** The two-minute countdown is your heads-up that a brief loading screen is
coming. When the site goes offline, wait a minute or two and refresh the page. The
replacement server will have taken over, and the dashboard will be back.

---

## Technical Details

### How the Warning Is Detected

The AWS endpoint checked is:

```
http://169.254.169.254/latest/meta-data/spot/termination-time
```

This is a link-local address (169.254.x.x) that is only reachable from within an EC2
instance — it is completely inaccessible from the public internet. AWS returns:

- **HTTP 200** with an ISO 8601 timestamp (e.g. `2024-11-14T12:34:56Z`) when a
  termination notice has been issued.
- **HTTP 404** during normal operation (no termination pending).

The check uses **IMDSv2** (Instance Metadata Service version 2), which requires a
short-lived session token before the metadata can be read. This is the current AWS
security best practice — it prevents certain categories of server-side request forgery
attacks.

On **non-spot or non-AWS environments** (standard on-demand instances, local development
machines, or any future cloud migration), the link-local address is simply unreachable.
The connection attempt fails immediately, the error is caught silently, and the banner
never appears. No configuration is required to make the feature safe on non-spot
infrastructure — it degrades gracefully by design.

### How the Proactive Replacement Works

When AWS publishes a spot interruption warning, it also emits an event through its
**EventBridge** notification system — a broadcast channel that other AWS services can
subscribe to. A small function (`spot_preempt`) listens for this event and immediately
tells the Auto Scaling Group to launch a second server instance alongside the one being
reclaimed.

A second function (`eip_reassociate`) handles the IP address during the transition. It
detects that a replacement is already in progress and holds off on moving the stable
public IP address until the old server actually shuts off — that way, the old server
keeps serving the live dashboard uninterrupted for the full two-minute window.

The moment AWS terminates the old server, a third function (`spot_restored`) moves the
public IP address to the replacement, resets the server count back to one, and clears
the in-progress flag. The replacement server then starts serving traffic normally.

### Modularity

Each layer of this system is independently removable:

- **To switch from spot to standard (on-demand) pricing:** delete `terraform/spot_preempt.tf`
  and run a deploy. The EventBridge rules, the two new Lambda functions, and the SSM flags
  are removed. The EIP Lambda and dashboard banner both detect the absence of the SSM
  parameters at runtime and fall back to their original behaviour — no Python code changes
  needed.

- **The sleep/wake system has been removed** (as of 2026-04-15). The server now runs continuously. The spot interruption Lambdas and toast notification remain active.

Re-adding the spot interruption feature after switching to on-demand pricing is a matter of restoring `terraform/spot_preempt.tf` and running a deploy.
