# Deploy --provision Does Not Bootstrap Fresh Spot Instances

**Date:** 2026-04-14
**Severity:** High (deploy failed at Step 1c, all subsequent steps blocked)
**Affected component:** `scripts/deploy/setup.sh` — Step 1c (kubectl config check)

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy failed at Step 1c with:

```
=== Step 1c: Ensuring kubectl config is accessible ===

ERROR: /etc/rancher/k3s/k3s.yaml not found on EC2.
  K3s is not installed on this instance. This happens when the ASG
  launched a fresh spot instance that has not been set up yet.

  Run the bootstrap script first:
    ./scripts/bootstrap_ec2.sh <ssh-host>

  Then re-run this deploy.
```

Here is what happened, step by step:

1. The deploy was run with `--provision`, which tells Terraform to set up or update the AWS infrastructure. In this case, the Auto Scaling Group had launched a brand-new spot instance to replace the previous one.

2. Terraform finished successfully — the instance was running and SSH was reachable.

3. The deploy moved on to Step 1c, which checks whether K3s (the lightweight Kubernetes system) is installed by looking for its configuration file at `/etc/rancher/k3s/k3s.yaml`.

4. On this fresh spot instance, K3s had never been installed. The ASG launches new instances from a plain Ubuntu image with nothing pre-installed. All required software (K3s, Docker, Helm, MariaDB, etc.) must be installed separately.

5. An earlier fix (the "chmod k3s.yaml not found" incident from the same day) had already added a check that prints a clear error message instead of crashing on `chmod`. But the fix only printed instructions telling the user to manually run the bootstrap script (`bootstrap_ec2.sh`) and then re-run the deploy. This meant two extra manual steps and roughly 10 minutes of wasted time each time a fresh spot instance was launched.

6. Since the `--provision` flag was already being used (which means "I know the infrastructure might be new"), the deploy should have been smart enough to detect the fresh instance and install the required software automatically, without making the user run a separate command.

---

## What was changed

**`scripts/deploy/bootstrap.sh`** (new file)

Created a new deploy module that installs all required software on a fresh spot instance. This is a lightweight version of the full `bootstrap_ec2.sh` script, designed specifically for the spot-instance-replacement case where:
- There is no database backup to restore (spot instances start fresh every time)
- The credentials are already stored in `.env.deploy` (no interactive prompts needed)
- The rest of the deploy pipeline will handle everything else (Helm, Kafka, MLflow, etc.)

The module installs:
- Base packages (MariaDB, Docker, curl, unzip)
- AWS CLI v2 (detects ARM vs Intel automatically)
- K3s (lightweight Kubernetes)
- Helm (Kubernetes package manager)

And sets up:
- kubectl configuration (so the deploy can talk to the cluster)
- Kubernetes namespace and storage volumes (PV/PVC) that Airflow needs
- MariaDB with a fresh empty database and the `airflow_user` account
- The `db-credentials` Kubernetes secret (database credentials that Airflow and the dashboard read)

**`scripts/deploy/setup.sh`**

Changed the "K3s not found" check. Previously, it always printed an error and stopped. Now it checks whether `--provision` was used:
- If `--provision` was used: runs the auto-bootstrap automatically and continues the deploy.
- If `--provision` was NOT used: prints the same error as before, but now also suggests re-running with `--provision` as an option (in addition to the manual bootstrap).

```bash
# Before: always stopped and told the user to run bootstrap manually.
if ! ssh "$EC2_HOST" "test -f /etc/rancher/k3s/k3s.yaml"; then
    echo "ERROR: ..."
    exit 1
fi

# After: auto-bootstraps when --provision was used.
if ! ssh "$EC2_HOST" "test -f /etc/rancher/k3s/k3s.yaml"; then
    if [ "${PROVISION:-false}" = true ]; then
        step_auto_bootstrap  # install K3s, Docker, Helm, MariaDB, etc.
    else
        echo "ERROR: ..."
        exit 1
    fi
fi
```

**`scripts/deploy.sh`**

Added `source "$DEPLOY_DIR/bootstrap.sh"` so the new module is loaded alongside the other deploy modules.

**`.env.deploy.example`**

Added three new variables that the auto-bootstrap needs to create the MariaDB user and the `db-credentials` Kubernetes secret:
- `DB_PASSWORD` — password for the MariaDB `airflow_user` account
- `SEC_EDGAR_EMAIL` — email sent in SEC EDGAR API request headers (required by SEC's usage policy)
- `SLACK_WEBHOOK_URL` — Slack webhook for alerts (can be left blank for log-only mode)

These values were previously only entered interactively when running `bootstrap_ec2.sh`. Storing them in `.env.deploy` means the auto-bootstrap can read them without prompting.

---

## Why this didn't happen before

The first version of the "k3s.yaml not found" fix (earlier on 2026-04-14) only added a helpful error message — it did not attempt to fix the problem automatically. That was a reasonable first step because it was not clear at the time whether the deploy script had enough information to bootstrap the instance on its own.

It turned out that all the required credentials are already in `.env.deploy` (or could easily be added there), and the installation steps from `bootstrap_ec2.sh` could be extracted into a lighter function that runs without any interactive prompts. The `--provision` flag is a natural trigger for this behavior, since a user who passes `--provision` already expects the script to handle infrastructure setup.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/bootstrap.sh` | New module: auto-installs K3s, Docker, Helm, MariaDB, and creates K8s prerequisites on fresh spot instances |
| `scripts/deploy/setup.sh` | Step 1c now auto-bootstraps when `--provision` is used, instead of always failing on fresh instances |
| `scripts/deploy.sh` | Sources the new `bootstrap.sh` module |
| `.env.deploy.example` | Added `DB_PASSWORD`, `SEC_EDGAR_EMAIL`, `SLACK_WEBHOOK_URL` for auto-bootstrap |
