# Spot Recovery Architecture

## Overview

This pipeline runs 24/7 on an AWS **Spot Instance** — spare server capacity that AWS rents out at a 60–80% discount compared to standard pricing. The trade-off is that AWS can reclaim it at any time if the capacity is needed elsewhere, giving exactly a two-minute warning before the server shuts down. To handle this gracefully, the infrastructure is built around a self-healing loop: the moment a warning fires, a replacement server starts booting automatically. By the time the old server goes down, the replacement is already nearly ready. The dashboard comes back on its own within 3–5 minutes, with no manual intervention needed.

---

## The Recovery Sequence

When AWS issues a spot interruption warning, the following steps happen automatically:

1. AWS emits the warning through **EventBridge** — AWS's internal notification broadcast system.
2. The `spot_preempt` Lambda function (a small, event-driven piece of code that runs without a dedicated server) receives the warning and immediately tells the Auto Scaling Group to launch a replacement instance alongside the existing one.
3. The replacement boots from a **pre-baked AMI** (a saved snapshot of the server with all software already installed), so it's ready in 3–5 minutes rather than the 20+ minutes a fresh setup would take.
4. The `eip_reassociate` Lambda detects that a replacement is already booting and holds off on moving the **Elastic IP** (the stable public address that users connect to), keeping the old server online and serving traffic for the full two-minute window.
5. AWS terminates the old instance after two minutes.
6. The `spot_restored` Lambda detects the termination, moves the Elastic IP to the replacement instance, resets the Auto Scaling Group back to one running instance, and clears the in-progress status flags stored in **SSM Parameter Store** (AWS's key-value store for configuration and state).
7. **CloudFront** (AWS's global content delivery network) serves a static "switching servers" loading page from **S3** (cloud file storage) during the brief gap between the old server going down and the new one being ready, so visitors see a friendly page rather than a browser error.
8. The dashboard comes back automatically when the replacement is ready.

### Timing

| Time | What is happening |
|------|------------------|
| **0:00** | Warning fires. Amber countdown banner appears on the dashboard. Replacement server starts booting. |
| **0:00 – 2:00** | Old server keeps running normally. Visitors can still use the dashboard. |
| **2:00** | Old server is terminated. Elastic IP moves to the replacement instance. Loading page becomes visible. |
| **2:00 – 3–5 min** | Replacement finishes booting. CloudFront loading page is shown while it starts up. |
| **~3–5 min** | Replacement is ready. Dashboard comes back automatically. |

---

## Key Components

### Auto Scaling Group (ASG)

An Auto Scaling Group is an AWS feature that maintains a target number of running servers. Normally it keeps exactly one spot instance running. When a spot warning fires, `spot_preempt` temporarily raises the target to two, causing a second instance to launch. After the old one terminates, `spot_restored` lowers the target back to one.

### Elastic IP (EIP)

A regular server's IP address changes every time it restarts. An Elastic IP is a static public address that stays fixed and can be moved between servers. Because the dashboard's domain name points at this address, moving the EIP to the replacement instance is what makes the dashboard reachable again without any DNS changes.

### Pre-Baked AMI

An AMI (Amazon Machine Image) is a complete snapshot of a server — its operating system, installed packages, configuration files, and application code. By building a fresh AMI after each significant deploy, new instances boot directly into a ready state in 3–5 minutes instead of spending 20+ minutes installing software from scratch.

### Lambda Functions

Lambda is AWS's "serverless" compute service: small functions that run in response to events, with no server to manage.

| Function | File | What it does |
|----------|------|-------------|
| `spot_preempt` | `terraform/lambda/spot_preempt.py` | Receives the spot warning from EventBridge and immediately triggers a replacement instance launch. |
| `eip_reassociate` | `terraform/lambda/eip_reassociate.py` | Manages the Elastic IP. During a spot replacement, it detects the in-progress flag and waits before moving the IP, so the old server stays live for the full two minutes. |
| `spot_restored` | `terraform/lambda/spot_restored.py` | Detects that the old instance is gone, moves the EIP to the new instance, resets the ASG to desired=1, and clears the SSM status flags. |

All three are defined in `terraform/cloudfront.tf`.

### CloudFront + S3 Loading Page

CloudFront is AWS's content delivery network — it sits in front of the server and can serve cached content. During the brief gap between the old instance going down and the new one being ready, CloudFront falls back to a static "switching servers" HTML page stored in S3, so visitors see a friendly informational page instead of a browser connection error.

### Dashboard Amber Banner

When a spot notice is first detected (by polling the EC2 instance metadata endpoint every five seconds), the dashboard shows a visible amber countdown banner in the bottom-right corner of every page. This is handled by `dashboard/spot.py`. The banner shows exactly how many minutes and seconds remain, a note that a replacement is already starting, and disappears once the server goes down.

---

## How to Keep the System Healthy

**Bake a new AMI after significant changes.**
Any time the application code, dependencies, or server configuration changes meaningfully, the saved AMI should be updated so the next replacement boots with current software:

```
./scripts/deploy.sh --bake-ami
```

**Switching to standard (non-spot) pricing.**
If uptime guarantees become more important than cost savings, the spot interruption infrastructure can be removed without touching any Python code. Delete `terraform/spot_preempt.tf` and run a deploy. This removes the EventBridge rules, the Lambda functions, and the SSM flags. Both the `eip_reassociate` Lambda and the dashboard banner detect the absence of the SSM parameters at runtime and fall back to their normal behaviour automatically.

Switching back is the reverse: restore `terraform/spot_preempt.tf` and redeploy.

---

## What Visitors Experience

1. **Amber banner appears.** A small amber panel appears in the bottom-right corner of the page showing a live countdown (e.g. `1:47`). The dashboard remains fully usable during this window.
2. **Countdown reaches zero.** The server goes offline. The browser may show a brief connection error or spin before CloudFront's loading page kicks in.
3. **Loading page appears.** A static "switching servers" page is served, confirming the site will be back shortly.
4. **Dashboard comes back.** Within 3–5 minutes of the warning, the replacement server is live and the loading page redirects automatically. No browser refresh is needed.

No data is lost during this process. All pipeline data lives in Snowflake (an external cloud database), which is completely unaffected by the server restart.
