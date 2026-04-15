# Deploy Guide

Everything you need to know about deploying this project lives here.

---

## The One Rule

**Always deploy by running `./scripts/deploy.sh`.** Never run `kubectl`, `rsync`, `docker`, or `terraform` commands directly — they bypass the safety checks, skip steps, and leave things in an inconsistent state. The deploy script handles all of that in the right order.

---

## Quick Reference

| What changed | Command to run |
|---|---|
| Only Airflow DAG `.py` files | `./scripts/deploy.sh --dags-only` |
| Dashboard, Python files, CSS, or anything else | `./scripts/deploy.sh` |
| Any file inside `terraform/` | `./scripts/deploy.sh --provision` |

---

## Each Option Explained

### `./scripts/deploy.sh` — Standard deploy (most common)

Run this whenever you've changed dashboard code, Python files, CSS, Kubernetes manifests, or anything outside of `airflow/dags/` and `terraform/`.

It validates your code, copies files to the server, builds the Docker image, restarts the right pods, and verifies everything came back up healthy. Takes about **30–45 minutes** on a cold server.

---

### `./scripts/deploy.sh --provision` — Infrastructure + deploy

Run this whenever any file inside `terraform/` has changed — or when you're setting up a brand-new server for the first time.

"Provisioning" means telling AWS to create or update the underlying infrastructure: the server, networking rules, Lambda functions, and so on. The `--provision` flag runs that step first, then continues with the standard deploy automatically. You don't need to run two commands.

**If you're unsure whether you need `--provision`:** check if any files in `terraform/` appear in your git diff. If yes, use `--provision`. If no, use the standard deploy.

Takes about **35–50 minutes** total (Terraform + full deploy).

---

### `./scripts/deploy.sh --dags-only` — Fast path for DAG changes

Run this when you've only changed `.py` files inside `airflow/dags/` — the Airflow pipeline logic.

It skips building Docker images, skips restarting most pods, and only syncs the DAG files and bounces the relevant Airflow services. Much faster than a full deploy.

Takes about **5–7 minutes**.

> Do not use this for changes outside of `airflow/dags/`. Those changes won't be picked up.

---

### `./scripts/deploy.sh --snowflake-setup` — Bootstrap Snowflake (one-time)

Run this once when setting up a brand-new Snowflake account, or after a full teardown. It creates the warehouse, database, schemas, role, and user that the pipeline expects to exist.

Safe to re-run — it uses "create if not exists" logic, so running it again on an existing setup won't break anything.

Requires three extra environment variables in `.env.deploy`: `SNOWFLAKE_ADMIN_USER`, `SNOWFLAKE_ADMIN_PASSWORD`, and `SNOWFLAKE_PASSWORD`.

Takes about **10–35 minutes** (Snowflake setup + full deploy).

---

### `./scripts/deploy.sh --fix-ml-venv` — Quick ML environment repair

Run this if a deploy printed a `WARNING` during the ML environment setup step (`step_setup_ml_venv`). It repairs the ML Python environment inside the running scheduler pod in about 60 seconds, without restarting anything or rebuilding Docker images.

Much faster than a full redeploy when only the ML environment is broken.

---

### `./scripts/deploy.sh --bake-ami` — Snapshot the server (optional)

Run this to create a fresh "golden AMI" — a saved snapshot of the server's current state. When the ASG launches a new instance (after waking from sleep), it boots from this snapshot instead of starting from scratch.

**Why it matters:** booting from a fresh AMI takes 3–5 minutes. Booting without one takes 60+ minutes because K3s, Docker, and all the dependencies need to be installed from scratch.

A new AMI is baked automatically in the background after every successful full deploy, so you rarely need to run this manually. Use it if you want to force a fresh snapshot outside of a deploy.

---

## What Happens During a Full Deploy

Here's what the script does, in plain language:

1. **Verifies SSH** — confirms the always-on server is reachable before proceeding
2. **Validates Python syntax** in all DAG files — stops early if any file has a syntax error
4. **Copies files to the server** via rsync (only sends files that actually changed)
5. **Builds the Airflow Docker image** on the server (runs in the background)
6. **Deploys Kafka and MLflow** (runs in the background, in parallel with step 5)
7. **Applies Kubernetes secrets** — Snowflake credentials, dbt profiles, Flask auth tokens
8. **Builds and pushes the Flask dashboard image** to ECR (runs in the background)
9. **Runs Helm upgrade** to update the Airflow configuration
10. **Waits** for all background jobs (steps 5, 6, 8) to finish
11. **Restarts Airflow pods** (scheduler, dag-processor, triggerer) in parallel
12. **Rebuilds the ML Python environment** inside the scheduler pod
13. **Resets Kafka consumer offsets** to prevent duplicate processing
14. **Cleans up** failed or evicted pods
15. **Verifies Flask** is healthy and responding
16. **Bakes a new AMI** in the background (so future boots are fast)
17. **Prints a summary** of any warnings or errors from the log

---

## How Long Does Each Command Take?

| Command | Typical time |
|---|---|
| `./scripts/deploy.sh` | 30–45 min |
| `./scripts/deploy.sh --provision` | 35–50 min |
| `./scripts/deploy.sh --dags-only` | 5–7 min |
| `./scripts/deploy.sh --snowflake-setup` | 10–35 min |
| `./scripts/deploy.sh --fix-ml-venv` | ~1 min |
| `./scripts/deploy.sh --bake-ami` | 15–25 min |

The AMI bake after a full deploy runs in the background — the deploy itself is "done" before the bake finishes, so you can keep working.

---

## Signs Something Went Wrong

The deploy script prints a summary at the end. If something failed, look here first.

| What you see | What it means | What to do |
|---|---|---|
| `WARNING` in ml-venv step | ML Python environment didn't build cleanly | Run `./scripts/deploy.sh --fix-ml-venv` |
| `ImagePullBackOff` on Flask pod | ECR pull failed (token expired or image missing) | Re-run `./scripts/deploy.sh` |
| Flask pod not Ready after 90s | Pod is stuck starting up | Check `kubectl describe pod` on the server for the reason |
| Terraform "Error acquiring the state lock" | A previous Terraform run crashed mid-way | Run `./scripts/deploy/terraform.sh apply` manually; Terraform will prompt to unlock |
| SSH connection refused at start | Server is asleep or still booting | Wait 2–3 minutes and retry, or check AWS Console for instance status |
| "Unable to locate credentials" | AWS SSO session expired | The script opens a browser login automatically — complete the SSO login and retry |

Every deploy writes its full output to `/tmp/deploy-last.log`. If the summary isn't enough, that file has the complete history.

---

## Related Docs

- [COSTS.md](COSTS.md) — how spot pricing keeps infrastructure costs low
- [ON_DEMAND_ARCHITECTURE.md](ON_DEMAND_ARCHITECTURE.md) — how the server infrastructure is set up
- [AWS_SSO_LOGIN.md](AWS_SSO_LOGIN.md) — AWS credential setup
- [AMI_CANCEL_AND_REPLACE.md](AMI_CANCEL_AND_REPLACE.md) — how AMI baking and replacement works
