# Monthly Costs

Everything this project costs to run, what's free, and how to shut down and restore.

---

## Monthly Breakdown

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| EC2 t3.large (on-demand, us-east-1) | ~$60 | 2 vCPU, 8 GB RAM — runs all services |
| EBS 100 GiB gp3 | ~$8 | Encrypted root volume |
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
| **Total** | **~$70–75** | |

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
- **EBS charges continue** — the 100 GiB volume stays attached (~$8/month)
- **Elastic IP charges begin** — AWS charges for an EIP not attached to a running instance (~$3.65/month)
- **Total idle cost: ~$12/month**

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

Full restoration takes about 30–45 minutes. See [DEPLOY.md](DEPLOY.md) for the complete deploy guide.
