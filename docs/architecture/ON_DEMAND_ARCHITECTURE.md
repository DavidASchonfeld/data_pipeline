# Server Architecture

How the dashboard stays available around the clock at a low cost.

---

## What It Does

The server runs continuously on AWS spot pricing. Spot instances are spare computing capacity that AWS offers at a significant discount — typically 70–80% cheaper than standard on-demand pricing — in exchange for the possibility that AWS may reclaim the server with a short warning if demand elsewhere spikes.

To handle that possibility gracefully, the system automatically boots a replacement instance and moves the public IP address over before the original goes offline. From a visitor's perspective, the dashboard stays available with no action required.

| Approach | Monthly Cost | Availability |
|----------|-------------|--------------|
| Always-on (standard pricing) | ~$70 | Continuous |
| **Always-on (spot pricing)** | **~$19–20** | **Continuous, with automatic recovery** |

---

## How Spot Recovery Works

When AWS decides to reclaim a spot instance, it sends a 2-minute warning before termination. The pipeline uses this window to start a replacement:

1. **Warning received.** An EventBridge rule detects the 2-minute termination notice and triggers the `spot-preempt` Lambda function.

2. **Replacement starts.** The Lambda tells the Auto Scaling Group to launch a second instance immediately (before the first one goes down). This gives the replacement the full boot window to get ready.

3. **Elastic IP moves.** Once the original instance terminates, another Lambda (`eip-reassociate`) automatically moves the static public IP address to the replacement instance. The public URL stays the same.

4. **Normal operation resumes.** The `spot-restored` Lambda resets the Auto Scaling Group back to its normal size (one instance) and clears any status flags. The dashboard shows a brief countdown notification during the transition.

---

## Key Components

- **Auto Scaling Group (ASG)** — Keeps one spot instance running at all times. Normally set to min=1, max=1. During a spot replacement, it temporarily scales to max=2 to let the replacement boot before the original terminates.

- **Elastic IP (EIP)** — A static public IP address that moves with the pipeline regardless of which physical server is running. Visitors always use the same address.

- **Pre-Baked AMI** — A saved snapshot of the fully configured server, including all installed software. New instances boot from this snapshot so they are ready in a few minutes rather than needing to install everything from scratch.

- **Spot Interruption Lambdas** — Two Lambda functions handle the replacement sequence automatically: `pipeline-spot-preempt` (starts the replacement) and `pipeline-spot-restored` (moves the IP and resets the ASG after the old instance is gone).

- **Dashboard Toast Notification** — When a spot interruption is detected, the dashboard displays a visible countdown so anyone actively using it knows a brief interruption is coming.

---

## How to Use

**The server is always running.** There is no manual start or stop — just SSH or open the dashboard.

**If you need to SSH in:**
```bash
ssh ec2-stock
```

**Bake a new AMI** after significant changes (new Docker image, package updates, etc.):
```bash
./scripts/deploy.sh --bake-ami
```
This creates a fresh snapshot so future replacement instances include the latest changes.

---

## What Happens When a New Instance Starts

Whether launching after a spot interruption or after a full `--provision` deploy, every new instance follows the same boot sequence:

1. **Boots from the AMI snapshot** — all software is already installed.
2. **Kubernetes starts** — K3S initializes and begins launching application containers.
3. **Services come online** — Kafka, Airflow, and the Flask dashboard start inside their containers.
4. **The dashboard connects to Snowflake** — the application establishes its data warehouse connection.
5. **Health check passes** — the instance registers as healthy and begins serving traffic.

This process typically takes 3–5 minutes from instance launch to a live dashboard.
