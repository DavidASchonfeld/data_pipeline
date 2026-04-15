# Terraform Data Loss Protection

## The Design

With the Auto Scaling Group setup, instances are designed to be disposable. All pipeline data
(financial filings, weather readings, anomaly scores) lives in Snowflake — not on the EC2 instance.
When a spot instance is replaced, the new one boots from a pre-baked AMI snapshot that already has
all software installed. No manual intervention is required for normal spot replacement.

---

## What Lives Where

| Data | Where it lives | Safe if instance is replaced? |
|---|---|---|
| Stock financials, weather readings, anomaly scores | Snowflake | Yes — Snowflake is independent of EC2 |
| Airflow DAG run history and logs | EC2 disk (not Snowflake) | Lost on replacement — acceptable, pipeline re-runs clean |
| MLflow experiment runs and model artifacts | EC2 disk | Lost on replacement — acceptable, model re-trains on next run |
| Kafka log segments (not yet consumed) | EC2 disk | Lost on replacement — Airflow will re-queue on restart |
| Software and configuration | Baked into the AMI | Pre-installed on every new instance |

---

## How Recovery Works

When a spot instance is replaced (either because AWS reclaimed it or due to manual intervention):

1. The ASG launches a new instance from the latest baked AMI
2. The `eip_reassociate` Lambda moves the static public IP to the new instance automatically
3. K3s starts, pods come online, and the dashboard reconnects to Snowflake
4. CloudFront serves the static "switching servers" page from S3 during the brief window while
   the new instance is booting, so visitors see a clean loading page rather than an error

The deploy script (`./scripts/deploy.sh`) is **not** required for a spot recovery — the AMI already
has everything. It **is** required after code changes (new DAGs, updated Docker image, etc.).

---

## Keeping the AMI Current

Bake a fresh AMI after significant changes (new Docker image, package updates, etc.):

```bash
./scripts/deploy.sh --bake-ami
```

This creates a snapshot of the running instance so future replacements start from the latest state.
If you skip this step after major changes, the replacement instance will boot from an older snapshot
and need a `./scripts/deploy.sh` run to catch up.

---

## EBS Settings

The root EBS volume uses `delete_on_termination = true` — it is safe to delete because all important
data is in Snowflake. There is no need for `delete_on_termination = false` or manual volume
detachment. The volume is 30 GiB (gp3), sized for the OS and software only, not for storing
pipeline data.

---

## What Was NOT Changed

`prevent_destroy = true` was deliberately left out. Blocking `terraform destroy` would prevent
legitimate teardown if the project is temporarily shut down to save costs.
