# Monthly Costs

Everything this project costs to run, what's free, and how to shut down and restore.

---

## Monthly Breakdown

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| EC2 t4g.large (spot, always-on) | ~$14–15 | Continuous spot instance; AWS can reclaim with 2-min notice — handled automatically |
| EBS snapshot (AMI storage) | ~$1.50 | 30 GiB snapshot preserves the full server state |
| Elastic IP (attached) | ~$0 | No charge while attached to a running instance |
| Lambda (spot-preempt / spot-restored / eip-reassociate) | $0 | Under free tier; only fires during spot interruptions |
| EventBridge (spot rules) | $0 | Spot interruption event rules; no timer rule |
| SSM Parameter Store | $0 | Spot replacement flags; standard parameters are free |
| Auto Scaling Group | $0 | Free; manages spot lifecycle |
| SNS topic | $0 | Free at this usage level |
| CloudFront (dashboard proxy) | $0 | Free tier: 1 TB transfer + 10M requests/month; proxies to EC2 with S3 failover |
| S3 (loading page) | ~$0 | One static HTML file; accessed only during spot transitions |
| ECR (container registry) | ~$0.10 | One Docker image; lifecycle policy removes old versions |
| Snowflake (XSMALL warehouse) | ~$2–5 | Auto-suspends after 60 seconds; batch gating limits use |
| SEC EDGAR API | Free | U.S. government API |
| Open-Meteo API | Free | Open-source weather API |
| Apache Kafka / Airflow / dbt / MLflow / K3S | Free | Open-source, runs on EC2 |
| GitHub | Free | Public repository |
| **Total** | **~$18–22** | |

---

## Cost Controls in Place

Several deliberate choices keep costs low:

- **Daily batch gate** — The stocks pipeline writes to Snowflake once per day, not on every hourly run. This prevents unnecessary warehouse activations.
- **Weather deduplication** — Before writing, the weather pipeline checks which rows already exist in Snowflake and only writes net-new rows.
- **Snowflake XSMALL warehouse with 60-second auto-suspend** — The warehouse spins down after one minute of inactivity and only spins up when a query runs.
- **Dashboard query cache** — The Flask dashboard holds Snowflake query results in memory (1 hour for financials/anomalies/health, 15 minutes for weather). Regardless of how many users load the page, Snowflake is queried at most a handful of times per hour. See [architecture/DASHBOARD_CACHE.md](architecture/DASHBOARD_CACHE.md).
- **Staleness monitor paused** — The staleness monitoring DAG is paused in production to avoid triggering Snowflake warehouse spin-ups every 30 minutes. It can be re-enabled on demand.
- **ECR lifecycle policy** — Untagged images (old versions) are automatically deleted after 1 day.
- **Airflow image imported directly into K3S** — The custom Airflow image is imported into the local containerd runtime instead of being pushed to ECR, avoiding storage charges for a 3+ GB image.
- **Spot pricing** — The EC2 instance runs continuously on spot pricing, which is 70–80% cheaper than standard on-demand pricing. AWS can reclaim a spot instance with a 2-minute warning, but the infrastructure handles this automatically by booting a replacement before the old server goes offline. See [ON_DEMAND_ARCHITECTURE.md](ON_DEMAND_ARCHITECTURE.md).
- **Pre-cached dashboard image (`imagePullPolicy: IfNotPresent`)** — The dashboard container image is baked into the AMI snapshot. On spot replacement, K3s uses the cached copy instead of re-downloading from ECR, saving ~30–60 seconds of boot time with no additional cost. The deploy script clears the cache on each new deploy so updates are always applied.
- **CloudFront failover page** — CloudFront sits in front of the dashboard as a reverse proxy (no caching). During the brief window when a spot instance is being replaced, CloudFront automatically serves a static loading page from S3 instead of showing a browser error. Both CloudFront and S3 are within free-tier limits at this traffic level — $0/month additional.

---

## What Happens If You Shut Down

### Stopping the EC2 instance

The server is designed to run continuously. Stopping it manually is not part of the normal workflow, but if needed:
- **EIP charges apply** — AWS charges ~$3.55/month for an Elastic IP not attached to a running instance
- **AMI snapshot charges apply** — the pre-baked AMI stores the full server state (~$1.50/month)
- **No EBS charges** — the root volume is deleted on termination (`delete_on_termination=true`); the AMI snapshot preserves the server state instead

### Full teardown (to $0/month)

To stop all charges:
1. Run `terraform destroy` from the `terraform/` directory — this removes the EC2 instance, security group, Elastic IP, and ECR repository
2. Snowflake: the free trial includes $400 in credits. After the trial, Snowflake only charges per query — if no queries run, no charges accrue
3. The EBS volume is deleted with the instance (configured in Terraform)

### Snowflake costs in detail

Snowflake charges for compute (warehouse running time), not for data at rest on the free trial. The XSMALL warehouse costs ~$2/credit, and one credit covers about an hour of compute. With the batch gating and auto-suspend in place, actual usage is a few minutes per day.

---

## Restoring After Shutdown

If you shut everything down and want to bring it back:

1. **Recreate AWS infrastructure:**
   ```bash
   cd terraform && terraform apply
   ```
   This recreates the EC2 instance, security group, Elastic IP, and ECR repository.

2. **Deploy the application:**
   ```bash
   ./scripts/deploy.sh --provision --snowflake-setup
   ```
   This updates your SSH access, bootstraps Snowflake objects, and deploys all code.

3. **Verify everything works:**
   Follow the [Verification Checklist](VERIFICATION.md) — 14 steps that confirm every component is running.

Full restoration takes about 30–45 minutes from a cold start (new instance, no AMI). If the ASG launches from the pre-baked AMI, recovery is ~3–5 minutes. See [DEPLOY.md](DEPLOY.md) for the complete deploy guide.

---

## Spot + ASG Architecture

The EC2 instance runs as a spot instance managed by an Auto Scaling Group (ASG) rather than as a standalone on-demand instance.

**Why spot?** t4g.large spot in us-east-1 runs ~$0.02–0.021/hour vs ~$0.0832/hour on-demand — roughly a 75% discount. The workload (batch DAGs, Kafka, K3S) tolerates brief interruptions, making spot a good fit.

**Why ASG instead of a standalone spot request?** The ASG handles spot interruption automatically: when AWS reclaims the instance, the ASG requests a replacement. It also enables the lifecycle hook pattern below.

**Why no ALB?** An Application Load Balancer costs a minimum of ~$16/month regardless of traffic — more than the EC2 instance itself at spot prices. Since this project has a single instance and no zero-downtime requirement, the instance's Elastic IP is used directly instead.

**Always-on spot:** The ASG keeps one spot instance running at all times (min=1, desired=1). If AWS reclaims the spot instance with a 2-minute warning, the spot-preempt Lambda immediately boots a replacement, and the EIP is transferred automatically after the old instance terminates.

**Lambda lifecycle hook:** A small Lambda function is triggered on ASG `EC2_INSTANCE_LAUNCHING` events. It re-attaches the Elastic IP to the new instance and sends an SNS notification on interruption. This keeps the public IP stable across spot replacements without manual intervention.

---

## Pre-Baked AMI

The ASG launch template points to a pre-baked AMI that already contains the full software stack: K3S, Kafka, Airflow image imported into containerd, and all dependencies.

This means a wake-up or spot replacement goes from ~30–45 minutes (full bootstrap from a base AMI) down to ~3–5 minutes (start services from snapshot). The AMI is managed as follows:

- Bake a new AMI: `./scripts/deploy.sh --bake-ami`
- The launch template is updated automatically after baking
- Re-bake after significant deploys (Docker image changes, package updates)
- Old AMIs are automatically cleaned up to avoid storage costs

---

## How We Got Here

The infrastructure went through three stages of cost optimization. Each stage preserved full functionality while reducing what was being paid for idle time.

| Configuration | Monthly Cost | How It Worked | Why It Changed |
|---|---|---|---|
| Standard on-demand | ~$70–75 | t3.large on-demand instance, always running | Correct and reliable, but paying full price for idle compute most of the day |
| Sleep/wake on-demand | ~$7–11 | Server shut down after 45 min of inactivity; woke up when someone visited the URL | Visitors had to wait 3–5 minutes for the server to start up on every first visit |
| **Spot instance, always-on (current)** | **~$18–22** | t4g.large ARM spot, always running; automatic replacement if AWS reclaims it | Eliminates the wake-up wait; spot pricing keeps costs 70–80% below standard on-demand |
