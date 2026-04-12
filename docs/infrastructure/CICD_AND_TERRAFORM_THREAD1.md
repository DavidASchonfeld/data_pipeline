# Thread 1 — Infrastructure + CI/CD

## What was added

### 1. Terraform — Port 32147 ingress (terraform/main.tf)

Added a second ingress rule to `aws_security_group.pipeline_sg` that exposes port **32147** (the Dashboard Kubernetes NodePort) publicly. Previously only port 22 (SSH) was open; all app traffic went through SSH tunnels.

- SSH (port 22) still restricted to `var.ssh_ingress_cidr` (your operator IP)
- Dashboard (port 32147) open to `0.0.0.0/0` — required for the public Dash app URL
- All other ports (Airflow UI, MLflow) remain SSH-tunnel only

**If being added to the project while it is being built, this must be added manually** — see Manual Steps below.

---

### 2. GitHub Actions — dbt test on PR (.github/workflows/dbt-test.yml)

CI workflow that runs `dbt test` automatically whenever a PR touches dbt files.

**Trigger:** `pull_request` on paths:
- `airflow/dags/dbt/**`
- `.github/workflows/dbt-test.yml`

**What it runs:**
```
dbt deps
dbt test --select tag:stocks tag:weather
```

Uses `profiles.yml` at repo root (already uses `env_var()` for all credentials).
Stack: Python 3.12, dbt-snowflake==1.8.0, ubuntu-latest runner.

---

## Snowflake Charges — Read This

The dbt-test workflow **connects to Snowflake and bills compute time** when it runs.

| Action | Does it trigger CI? | Snowflake charge? |
|---|---|---|
| `git commit` (local) | No | No |
| `git push` to a branch (no open PR) | No | No |
| `git push` to a branch **with an open PR** | **Yes** — if dbt files changed | **Yes** |
| Opening/reopening a PR touching dbt files | **Yes** | **Yes** |

**Plain `git commit` does NOT trigger it.** Only a `git push` to an open PR branch does, and only when `airflow/dags/dbt/**` files are in the diff.

Your warehouse auto-suspends after inactivity — a single `dbt test` run is a few seconds of compute, so the cost per run is very small (fractions of a credit). Still, be aware each qualifying PR push = one charge.

---

## Manual Steps Required

### Step A — Apply the Terraform change

```bash
./scripts/deploy/terraform.sh plan   # verify: should show aws_security_group updated in-place
./scripts/deploy/terraform.sh apply  # type "yes" at the prompt
```

Expected: `aws_security_group.pipeline_sg` updated in-place (no destroy/recreate). After apply, port 32147 will be reachable from the public internet on your EC2 instance.

### Step B — Add GitHub Secrets

Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add all four secrets:

| Secret name | Where to find it |
|---|---|
| `SNOWFLAKE_ACCOUNT` | Snowflake UI -> bottom-left account menu -> account identifier (e.g. `abc12345.us-east-1`) |
| `SNOWFLAKE_USER` | Your Snowflake login username |
| `SNOWFLAKE_PASSWORD` | Your Snowflake login password |
| `SNOWFLAKE_WAREHOUSE` | Snowflake UI -> Admin -> Warehouses (e.g. `COMPUTE_WH`) |

### Step C — Verify CI works

1. Create a branch and make a trivial change inside `airflow/dags/dbt/` (e.g. add a blank line to any model file)
2. Push the branch and open a PR
3. Watch the **dbt-test** check appear under the PR's Checks tab
4. Confirm green — if it fails, check the Actions log for auth errors (likely a missing/wrong secret)
