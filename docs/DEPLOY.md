# Deploy Guide

How to deploy code changes and restore the project after a shutdown.

---

## Why This Script Exists

Originally, deploying code changes meant manually transferring files to the EC2 server using tools like FileZilla (a drag-and-drop file transfer app). This was slow, error-prone, and easy to forget a file. `deploy.sh` replaces that with a single command that validates, syncs, builds, and restarts everything.

It also makes restoration easy. If the project is shut down temporarily to save on hosting costs (see [COSTS.md](COSTS.md)), `deploy.sh` can redeploy the entire application onto a fresh EC2 instance in about 30 minutes.

---

## How to Use It

| Command | What it does | Time |
|---------|-------------|------|
| `./scripts/deploy.sh` | Full deploy: validate, sync, build, restart | 7–10 min (cached), 20–30 min (from scratch) |
| `./scripts/deploy.sh --dags-only` | Fast path: sync DAG files and restart Airflow pods only | 5–7 min |
| `./scripts/deploy.sh --provision` | Run Terraform first (update security group with your current IP), then full deploy | 10–35 min |
| `./scripts/deploy.sh --snowflake-setup` | Bootstrap Snowflake objects (warehouse, database, schemas, role) before deploying. Safe to re-run. | 10–35 min |
| `./scripts/deploy.sh --fix-ml-venv` | Quick fix for the ML Python environment without restarting pods | ~60 sec |

---

## Prerequisites

1. **`.env.deploy` file** — Contains AWS and Snowflake credentials. Copy from `.env.deploy.example` and fill in your values.
2. **SSH config** — An entry for the EC2 instance in `~/.ssh/config` (e.g., `Host ec2-stock`).
3. **IAM role** — The EC2 instance must have an IAM role attached that allows ECR push/pull. Without it, deploy fails with "Unable to locate credentials." Fix: EC2 Console → select instance → Actions → Security → Modify IAM role.

---

## What Each Phase Does

### Phase 0: Terraform (`--provision` only)

Updates AWS Security Group rules with your current IP address so SSH works from your location. This is the only step that modifies AWS infrastructure.

### Phase 1: Setup and Sync (always runs)

- Creates required directories on EC2 and sets file permissions
- Validates Python syntax in all DAG files — catches errors before deploying
- Syncs DAG files, Helm values, and Kubernetes manifests to EC2 via rsync (a smart copy tool that only sends files that changed)
- Applies Kubernetes secrets (Snowflake credentials, dbt profiles, Flask auth)

### Phase 2: Parallel Builds (full deploy only)

Three independent tasks run at the same time to save time:

1. **Airflow image build** — Builds a custom Docker image containing dbt and ML libraries. The image is imported directly into K3S's container runtime (not pushed to ECR — this saves storage costs).
2. **Kafka deploy** — Applies the Kafka StatefulSet manifest, pre-pulls the Kafka image, and creates the message topics.
3. **MLflow deploy** — Deploys the MLflow experiment tracking server and fixes artifact storage paths.

### Phase 3: Helm Upgrade and Flask Deploy (full deploy only)

- Runs `helm upgrade` with a pinned chart version to update the Airflow configuration
- Builds the Flask dashboard Docker image on EC2 and pushes it to ECR
- Restarts the Flask pod with the new image

### Phase 4: Pod Restarts and Verification (always runs)

- Restarts all Airflow pods in parallel (scheduler, dag-processor, triggerer)
- Waits for each pod to become ready, with health checks
- Installs the ML Python environment (`ml-venv`) in the scheduler pod — this is rebuilt on every restart because `/opt/` is ephemeral
- Resets Kafka consumer group offsets to prevent duplicate processing
- Cleans up any failed or evicted pods

---

## How deploy.sh Relates to Terraform

They handle different layers:

| Tool | What it manages | When to use |
|------|----------------|-------------|
| **Terraform** | AWS infrastructure: EC2 instance, security group, Elastic IP, ECR repository, IAM role | When creating or recreating the AWS environment |
| **deploy.sh** | Application code: DAG files, Docker images, Helm values, Kubernetes manifests, secrets | Every time you change code or configuration |

Terraform creates the server. deploy.sh puts the application on it.

### Why Terraform

Infrastructure-as-Code (IaC) means the AWS setup is defined in code files (`terraform/main.tf`), not clicked together in the AWS Console. This matters for two reasons:

1. **Reproducibility** — If the project is shut down and restored later, `terraform apply` recreates the exact same infrastructure in minutes instead of manually clicking through AWS Console screens.
2. **Version control** — Changes to infrastructure are tracked in git, just like code changes.

Terraform files are in the `terraform/` directory. See `terraform/variables.tf` for configurable settings (instance type, region, EBS volume size).

---

## How Files Get From Your Mac to Running Pods

```
1. You edit code on your Mac (source of truth)
2. deploy.sh copies files to EC2 via rsync (only sends changes)
3. Kubernetes mounts the EC2 directory into pods via PersistentVolumes
4. Pods see the updated files at /opt/airflow/dags/
```

The files are not copied a third time. Kubernetes uses a "mount" — it makes the EC2 folder visible inside the pod, like plugging in a USB drive. The pod reads files directly from EC2's filesystem.

---

## Script Module Structure

The deploy script is split into focused modules in `scripts/deploy/`:

| File | Purpose |
|------|---------|
| `common.sh` | Shared variables, error handling, helper functions |
| `setup.sh` | EC2 directory creation, Python syntax validation |
| `sync.sh` | File sync via rsync, Kubernetes secret application |
| `airflow_image.sh` | Docker build + K3S containerd import |
| `airflow_pods.sh` | Helm upgrade, pod restarts, ml-venv setup |
| `flask.sh` | Flask image build/push to ECR, pod lifecycle |
| `kafka.sh` | Kafka StatefulSet deploy, topic creation |
| `mlflow.sh` | MLflow deployment, artifact root fix |
| `snowflake.sh` | Snowflake object bootstrap |
| `terraform.sh` | Terraform apply wrapper |

---

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| "Unable to locate credentials" | EC2 instance's IAM role is missing | EC2 Console → select instance → Actions → Security → Modify IAM role |
| Pod shows `ImagePullBackOff` | Docker image wasn't imported into K3S, or K3S garbage-collected it | Re-run `deploy.sh` |
| DAG not appearing in Airflow | Python syntax error in a DAG file | Check the deploy output — deploy.sh validates syntax before syncing |
| Helm upgrade timeout | Migration job from a previous failed upgrade is still present | deploy.sh deletes stale migration jobs automatically |

For more, see [operations/TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md).

---

## Deploy Log

Every deploy writes its output to `/tmp/deploy-last.log`. At the end, deploy.sh prints a summary of any warnings or errors found in the log.
