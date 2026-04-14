# Migration Job Timeout — kubectl wait Ignores Failed Jobs

**Date:** 2026-04-13
**Severity:** High (deploy failed at Step 2f, all subsequent steps blocked)
**Affected component:** `scripts/deploy/airflow_pods.sh` — Step 2f and Step 7 pre-check

---

## What was the problem

After deploying to a fresh spot instance via `./scripts/deploy.sh --provision --snowflake-setup`, the deploy hung for 10 minutes at Step 2f and then failed with:

```
error: timed out waiting for the condition on jobs/airflow-run-airflow-migrations
```

Here is what happened, step by step:

1. **Step 2d** runs `helm upgrade`, which creates two things at the same time: a fresh PostgreSQL database pod and a migration job. The migration job's sole purpose is to connect to PostgreSQL and set up the Airflow database tables (the "schema").

2. On a brand-new spot instance, the PostgreSQL pod has never existed before. It needs to create its storage folder from scratch, initialise the database, and start accepting connections. This takes anywhere from 2 to 5 minutes.

3. The migration job doesn't wait for PostgreSQL — it starts trying to connect immediately. When the connection fails, it waits a short time and tries again. Each time it fails, the wait before the next try roughly doubles: first 10 seconds, then 20, then 40, and so on. This is called "exponential backoff."

4. The job is only allowed 6 attempts total (Kubernetes' default retry limit). With the increasing wait times between attempts, all 6 attempts can be used up within about 5 minutes. If PostgreSQL still isn't ready by then, all retries are gone and the job is permanently marked as **Failed**.

5. **Step 2f** was using `kubectl wait --for=condition=complete` to check whether the migration job finished. This command has a blind spot: it only watches for the "Complete" status. When a job is marked "Failed," `kubectl wait` doesn't notice — it just keeps waiting, silently doing nothing, until the 600-second timeout runs out.

6. The deploy script treated the timeout as a generic error. There was no way to tell from the output whether the job was still running, had failed, or had never started.

---

## What was changed

**`scripts/deploy/airflow_pods.sh` — Step 2f (two-phase fix)**

**Phase 1: Wait for PostgreSQL before checking the migration job.**

A new `kubectl wait` call was added that pauses the script until the PostgreSQL pod reports that it is healthy and accepting connections (up to 300 seconds). This runs before checking the migration job at all. On a warm server where PostgreSQL is already running, this check passes instantly and adds zero delay.

```bash
# Before: no check — script jumped straight to the migration job wait.

# After: wait for PostgreSQL pod first.
kubectl wait pod/airflow-postgresql-0 \
    -n airflow-my-namespace \
    --for=condition=Ready \
    --timeout=300s
```

**Phase 2: Replace `kubectl wait` with a polling loop that detects both success and failure.**

Instead of a single `kubectl wait --for=condition=complete` call, the script now checks the migration job's status every 10 seconds. It looks for two possible outcomes:

- **Complete** — the job finished successfully. Proceed with the deploy.
- **Failed** — the job ran out of retries. The script automatically deletes the failed job, recreates a fresh one using the Helm chart template (since PostgreSQL is now confirmed ready, this new job connects on its first try), and continues polling.

If the recreated job also fails, the script stops immediately and prints the job's error logs so you can see what went wrong.

```bash
# Before: single kubectl wait that ignores Failed status.
kubectl wait job/airflow-run-airflow-migrations \
    --for=condition=complete \
    --timeout=600s

# After: polling loop that checks both Complete and Failed every 10s.
for i in $(seq 1 60); do
    STATUS=$(kubectl get job ... -o jsonpath='{.status.conditions...}')
    if Complete → exit 0
    if Failed  → delete job, recreate from Helm template, set RETRIED flag
    sleep 10
done
```

**`scripts/deploy/airflow_pods.sh` — Step 7 pre-check**

The same `kubectl wait` blind spot existed in the Step 7 pre-check (which runs before restarting Airflow pods). This was also replaced with a polling loop that detects both Complete and Failed conditions. If the migration job has failed at this point, the script prints the job's error logs and stops immediately — restarting pods against a broken schema would just get them stuck in `Init:0/1`.

---

## Why this didn't happen before

On the long-running server that predated the spot instance setup, PostgreSQL was always already running and fully initialised from previous deploys. The migration job connected on its first attempt and completed in under a second. `kubectl wait --for=condition=complete` returned almost instantly, and the blind spot around Failed jobs never mattered.

The spot instance Auto Scaling Group (introduced on 2026-04-13) brings up a brand-new server each time. PostgreSQL starts completely from scratch, creating a race condition between the database starting up and the migration job running out of retry attempts. Once the job entered the Failed state, the blind spot in `kubectl wait` turned a recoverable 5-minute delay into a silent 10-minute hang with no useful diagnostics.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_pods.sh` | Step 2f: added PostgreSQL readiness wait + replaced `kubectl wait` with polling loop that detects Failed jobs and auto-recreates them |
| `scripts/deploy/airflow_pods.sh` | Step 7 pre-check: replaced `kubectl wait` with polling loop that detects Failed jobs and fails early with logs |
