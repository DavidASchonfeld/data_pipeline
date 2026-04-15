# Loading Page and Boot-Time Optimization

What was added, why it was added, and how it helps visitors to the dashboard.

---

## What Changed

Two things were updated at the same time:

1. **A better loading page** — when the dashboard is waking up from sleep, visitors now see a polished page with a countdown timer that shows roughly how long they have to wait.

2. **Faster wake-ups** — a small technical change reduced the time it takes for the server to become ready after being woken up, saving about 30–60 seconds on each boot.

---

## Why There Is a Loading Page

The dashboard runs on a server that automatically goes to sleep when no one is using it. When someone visits the link and the server is sleeping, the system starts it back up — but that takes a few minutes.

Without a loading page, the visitor would just see a browser error ("site can't be reached") for the entire boot time. The loading page fills that gap: it shows the visitor that something is happening and refreshes automatically until the dashboard is ready.

### Cost savings

Running a server around the clock costs approximately **$70 per month** at standard pricing, or **$19–20/month** using AWS spot (spare-capacity) pricing. Because this dashboard is only used for a few hours at a time, most of that spending would go to waste.

The sleep/wake approach brings the monthly cost down to roughly **$7–11 per month** — a saving of about **$10/month** vs. always-on spot, or up to **$60/month** vs. always-on standard pricing. The 3–5 minute loading time on first visit is the trade-off for those savings.

---

## The Countdown Timer

The previous loading page had a small spinning circle but gave no sense of how long the wait would be. The updated page shows a large circular countdown timer so visitors know approximately when the dashboard will be ready.

The timer is an estimate, not a guarantee. The actual time depends on how quickly AWS starts the server and how fast the background services come online — both of which can vary slightly. If the timer reaches zero and the page is still loading, it switches to "Almost ready…" and continues checking.

The timer persists across the automatic 10-second page refreshes, so it counts down smoothly rather than resetting each time.

---

## The Boot-Time Optimization

**What it does:** When the server wakes up from sleep, it previously re-downloaded the dashboard application from a remote registry (Amazon ECR) before starting. This download added about 30–60 seconds to every wake-up.

**The fix:** The dashboard application image is now baked directly into the server snapshot (the AMI). On wake-up, the server finds the image already stored locally and skips the download entirely.

**Why this is safe for updates:** When a new version of the dashboard is deployed, the deploy script explicitly clears the locally cached image before applying the new one, so the server always pulls the freshly-built version during a deploy. Only on wake-up from sleep (where no new version is being deployed) does the cached image get used.

**Result:** Wake-up time drops from roughly 3.5–5 minutes to roughly 2.5–4 minutes with no change in cost.

---

## Design

The loading page uses the same colors and fonts as the main dashboard (dark navy background, blue accents, soft white text) so the transition from loading page to live dashboard feels seamless rather than jarring.

The color palette is centralized in `dashboard/design_tokens.py` — a single Python file that both the dashboard theme and the loading page reference. Changing a color in that file updates it everywhere.
