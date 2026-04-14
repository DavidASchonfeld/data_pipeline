# Pods Stuck in Init:0/1 — wait-for-airflow-migrations Never Completed

**Date:** 2026-04-13
**Severity:** High (deploy failed, dag-processor and triggerer never started)
**Affected component:** `scripts/deploy/airflow_pods.sh` — Step 2f (new), `airflow/helm/values.yaml`

---

## What was the problem

After the spot instance was replaced by the new Auto Scaling Group (introduced the same day), running `./scripts/deploy.sh --provision --snowflake-setup` failed at Step 7 with both the dag-processor and triggerer pods permanently stuck:

```
airflow-dag-processor-5658756d4d-bf6cq   0/2     Init:0/1   1 (4m34s ago)   10m
airflow-triggerer-0                       0/2     Init:0/1   1 (4m34s ago)   10m
```

The `Init:0/1` status means the pod has one init container that has not passed yet. That init container is called `wait-for-airflow-migrations`. Its job is to check that the Airflow database schema is fully set up before the main containers are allowed to start. It kept running for about five minutes, failing, restarting, and failing again — until the deploy script gave up waiting.

Here is what happened step by step:

1. **Step 2d** runs `helm upgrade`, which creates a background job called `airflow-run-airflow-migrations`. This job connects to the built-in PostgreSQL database and applies the full Airflow schema. Because `helm upgrade` is configured to return immediately rather than waiting for this job (the `useHelmHooks: false` setting), the deploy script moves on to the next steps right away.

2. On a fresh spot instance, the PostgreSQL database has never been set up before. It must first create its data directory, then start accepting connections, and only then can the migration job connect and apply the schema. This whole chain takes several minutes on a cold server.

3. By the time **Step 7** runs and restarts the Airflow pods, the migration job was still working its way through that chain. The new pods started up and their `wait-for-airflow-migrations` init containers immediately started polling to see if the schema was ready.

4. The init container polls for up to 60 seconds (the default timeout), finds the schema not ready, then exits with an error. Kubernetes restarts it after a short wait. The second attempt also found the schema not ready and was still running when the deploy script gave up.

5. Because the deploy script never explicitly waited for the migration job to finish, there was nothing preventing pods from starting before the schema existed.

---

## What was changed

**`scripts/deploy/airflow_pods.sh`**

A new Step 2f was added at the end of `step_helm_upgrade()`. After Helm returns, the script now pauses and waits for the migration job to report that it finished successfully — before doing anything else. This means every subsequent step, including the pod restart in Step 7, only runs once the database is fully set up.

```bash
# Before: helm upgrade returned immediately; no wait for the migration job.
# Script moved on to image build and pod restart regardless of migration status.

# After: explicit wait added right after helm upgrade returns.
ssh "$EC2_HOST" "
    if kubectl get job airflow-run-airflow-migrations -n airflow-my-namespace \
            --ignore-not-found --no-headers 2>/dev/null | grep -q .; then
        echo 'Migration job found — waiting for completion (up to 600s)...'
        kubectl wait job/airflow-run-airflow-migrations \
            -n airflow-my-namespace \
            --for=condition=complete \
            --timeout=600s
        echo 'Migration job complete.'
    else
        echo 'Migration job not found — migrations already complete from a prior deploy, skipping.'
    fi
"
```

The `if` check handles the case where the job no longer exists because it was already cleaned up from a previous successful deploy — in that case the schema is already in place and there is nothing to wait for.

**`airflow/helm/values.yaml`**

The init container timeout was raised from the default 60 seconds to 300 seconds. This is a belt-and-suspenders measure for situations where pods are restarted outside the deploy script (for example, a manual `kubectl delete pod`). With this change, the init container stays patient long enough to wait out a slow PostgreSQL cold-start on a fresh spot instance before giving up.

```yaml
# Before: no entry — chart default of 60s used.

# After:
waitForMigrations:
  migrationWaitTimeout: 300  # raised from default 60s
```

---

## Why this didn't happen before

On the long-running server that predated the spot instance setup, PostgreSQL was always already running and fully initialised from a previous deploy. The migration job only had to check whether any new revisions were needed (usually none), which takes under a second. The init container's 60-second timeout was more than enough.

The spot instance Auto Scaling Group, introduced on 2026-04-13, brings up a brand-new server each time the instance is replaced. PostgreSQL starts completely fresh, and the full chain from data directory creation to schema migration can take several minutes. This exposed the timing gap that had always existed but never mattered on a warm server.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_pods.sh` | Added Step 2f: `kubectl wait` for the migration job to complete before returning from `step_helm_upgrade()` |
| `airflow/helm/values.yaml` | Added `waitForMigrations.migrationWaitTimeout: 300` to give the init container enough time on a cold start |
