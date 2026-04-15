# On-Demand Architecture

How the dashboard saves money by only running when someone needs it.

---

## What It Does

The dashboard website sleeps when no one is using it and wakes up automatically when someone visits the link. Think of it like a computer going into sleep mode — it powers down when idle, then comes back to life when you need it.

This matters because a server that runs 24/7 costs money every hour, whether anyone is looking at it or not. Since this dashboard is typically used for a few hours at a time rather than around the clock, keeping it running all day wastes most of that spending. The on-demand approach means the server only runs (and only costs money) when someone is actually using it.

| Approach | Monthly Cost | Tradeoff |
|----------|-------------|----------|
| Always-on (standard pricing) | ~$70 | No wait time, but expensive for low-traffic use |
| Always-on (spot pricing) | ~$19–20 | Same availability, cheaper, but rare brief interruptions |
| **On-demand (sleep/wake)** | **~$7–11** | Brief loading time on first visit, significant savings |

---

## How It Works

1. **You share a link.** The dashboard has a stable public URL that never changes, regardless of whether the server is awake or asleep.

2. **Someone clicks the link.** If the server is already running, the dashboard loads immediately. If the server is asleep, the system detects this and starts waking it up.

3. **A loading page appears.** While the server boots up, the visitor sees a simple page that says the dashboard is starting. This page automatically refreshes every few seconds.

4. **The dashboard appears.** After about 3–5 minutes, the server is ready and the loading page redirects to the live dashboard. From this point on, everything works normally.

5. **The server goes back to sleep.** After 45 minutes with no one visiting, the system automatically shuts the server down to stop the meter. The next visitor will trigger a fresh wake-up.

---

## Key Components

A few pieces work together to make this possible:

- **API Gateway** — The stable URL that visitors use. It stays active even when the server is off, so the link always works. When the server is asleep, API Gateway serves the loading page and triggers the wake-up process.

- **Wake Lambda** — A small function that runs when a visitor arrives and the server is sleeping. It tells the Auto Scaling Group to start an instance, then serves the loading page while the server boots.

- **Sleep Lambda** — A function that runs on a timer (every 15 minutes) to check whether anyone has visited recently. If the server has been idle for 45 minutes, it shuts it down.

- **Pre-Baked AMI** — A snapshot of the fully configured server, including all software and settings. Instead of installing everything from scratch on each wake-up (which would take 30–45 minutes), the server boots from this snapshot and is ready in a few minutes.

- **Boot Script** — Runs automatically each time the server starts. It starts Kubernetes, launches all the services (Kafka, Airflow, the dashboard), and signals that the server is ready to serve traffic.

---

## How to Use

**Share the dashboard link:**
The public URL comes from Terraform. Run this to see it:
```bash
terraform output dashboard_url
```
Share that URL with anyone who needs access. It works whether the server is awake or asleep.

**Wake the server manually** (without waiting for someone to visit):
```bash
./scripts/deploy.sh --wake
```

**Put the server to sleep manually:**
```bash
./scripts/deploy.sh --sleep
```

**Bake a new AMI** after significant changes (new Docker image, package updates, etc.):
```bash
./scripts/deploy.sh --bake-ami
```
This creates a fresh snapshot so future wake-ups include the latest changes.

**Change the idle timeout:**
The default is 45 minutes. To adjust it, change the `idle_timeout_minutes` variable in the Terraform configuration (`terraform/variables.tf`).

---

## What Happens During the Loading Time

When the server wakes up, several things happen in sequence:

1. **The server boots from its snapshot** — AWS launches a new instance using the pre-baked AMI, which already has all the software installed.
2. **Kubernetes starts** — K3S (the lightweight Kubernetes distribution) initializes and begins launching all the application containers.
3. **Services come online** — Kafka, Airflow, and the dashboard application start up inside their containers.
4. **The dashboard connects to Snowflake** — The application establishes its connection to the data warehouse so it can serve live data.
5. **The loading page redirects** — Once the health check confirms everything is running, the loading page automatically sends the visitor to the live dashboard.

This entire process typically takes 3–5 minutes. The loading page refreshes automatically, so the visitor does not need to do anything — just wait for the dashboard to appear.
