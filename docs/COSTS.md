# Monthly Costs

Everything this project costs to run, what's free, and how to shut down and restore.

---

## Monthly Breakdown

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| EC2 t4g.large (spot, us-east-1) | ~$14–15 | 2 vCPU, 8 GB RAM ARM — managed by Auto Scaling Group |
| EBS 30 GiB gp3 | ~$2.40 | Encrypted root volume |
| Auto Scaling Group | $0 | Free; manages spot lifecycle |
| Lambda (lifecycle hook) | $0 | Under free tier at this invocation rate |
| SNS topic (spot interruption alerts) | $0 | Effectively free at this usage level |
| Elastic IP | $0 | Free while attached to a running instance |
| ECR (container registry) | ~$0.10 | Stores one Docker image; lifecycle policy removes old tags |
| Snowflake (XSMALL warehouse) | ~$2–5 | Auto-suspends after 60 seconds; batch gating limits activations |
| SEC EDGAR API | Free | U.S. government API, no key required |
| Open-Meteo API | Free | Open-source weather API, no key required |
| Apache Kafka | Free | Open-source, runs on EC2 |
| Apache Airflow | Free | Open-source, runs on EC2 |
| dbt | Free | Open-source |
| MLflow | Free | Open-source |
| K3S (Kubernetes) | Free | Lightweight Kubernetes, runs on EC2 |
| GitHub | Free | Public repository |
| **Total** | **~$19–20** | |

---

## Cost Controls in Place

Several deliberate choices keep costs low:

- **Daily batch gate** — The stocks pipeline writes to Snowflake once per day, not on every hourly run. This prevents unnecessary warehouse activations.
- **Weather deduplication** — Before writing, the weather pipeline checks which rows already exist in Snowflake and only writes net-new rows.
- **Snowflake XSMALL warehouse with 60-second auto-suspend** — The warehouse spins down after one minute of inactivity and only spins up when a query runs.
- **Dashboard query cache** — The Flask dashboard holds Snowflake query results in memory for 1 hour. Regardless of how many users load the page, Snowflake is queried roughly 4–5 times per hour. See [architecture/DASHBOARD_CACHE.md](architecture/DASHBOARD_CACHE.md).
- **Staleness monitor paused** — The staleness monitoring DAG is paused in production to avoid triggering Snowflake warehouse spin-ups every 30 minutes. It can be re-enabled on demand.
- **ECR lifecycle policy** — Untagged images (old versions) are automatically deleted after 1 day.
- **Airflow image imported directly into K3S** — The custom Airflow image is imported into the local containerd runtime instead of being pushed to ECR, avoiding storage charges for a 3+ GB image.

---

## What Happens If You Shut Down

### Stopping the EC2 instance (not terminating)

The instance stops running and EC2 charges stop, but:
- **EBS charges continue** — the 30 GiB volume stays attached (~$2.40/month)
- **Elastic IP charges begin** — AWS charges for an EIP not attached to a running instance (~$3.65/month)
- **Total idle cost: ~$6/month**

All data on the EBS volume is preserved. Restarting the instance restores everything except the public IP (which stays the same because of the Elastic IP).

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

**Lambda lifecycle hook:** A small Lambda function is triggered on ASG `EC2_INSTANCE_LAUNCHING` events. It re-attaches the Elastic IP to the new instance and sends an SNS notification on interruption. This keeps the public IP stable across spot replacements without manual intervention.

---

## Pre-Baked AMI

The ASG launch template points to a pre-baked AMI that already contains the full software stack: K3S, Kafka, Airflow image imported into containerd, and all dependencies.

This means a spot replacement goes from ~30–45 minutes (full bootstrap from a base AMI) down to ~3–5 minutes (mount EBS, start services). The AMI is rebuilt via Packer whenever a significant dependency changes and the launch template is updated in Terraform.
