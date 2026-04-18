# Deploy Script Appears Frozen After "Migration job complete."

**Date:** 2026-04-17
**Severity:** Low (deploy was running correctly; no output was printed during a long wait)
**Affected component:** `scripts/deploy/common.sh` — `_wait_bg` helper

---

## What was the problem

After running `./scripts/deploy.sh`, the terminal printed "Migration job complete." and then went completely silent for several minutes with no further output:

```
=== Step 2f: Waiting for Airflow database migrations to complete ===
PostgreSQL pod current status:
airflow-postgresql-0   0/1   Running   0     25h
PostgreSQL PVC phase: Bound
Polling PostgreSQL pod readiness (up to 600s)...
  Attempt 1/20 — PostgreSQL not Ready yet (phase: Running) — waiting 30s...
PostgreSQL pod is Ready (attempt 2/20).
Migration job found — polling for completion (up to 900s)...
  Still waiting for migration job (attempt 3/90, 30s elapsed)...
Migration job complete.

[nothing printed for several minutes]
```

With no activity on screen, it was impossible to tell whether the script had frozen, crashed silently, or was still running normally.

Here is what was actually happening:

1. Step 2f finished — the database migration job completed and the SSH session closed normally.

2. The script moved on to Phase 5, which waits for two background jobs that had been running in parallel the whole time: Kafka and MLflow. Both of those steps were kicked off as background processes near the top of the deploy (before Step 2f ran).

3. The function responsible for waiting on those background jobs, `_wait_bg`, simply called bash's built-in `wait` command and printed nothing until the job either succeeded or failed. Kafka deploys can take 7-10 minutes. During that entire window, the terminal showed nothing at all.

4. The deploy itself was progressing correctly the whole time. The silence was purely a cosmetic problem — there was just no code to print a "still working..." update while `_wait_bg` waited.

---

## What was changed

**`scripts/deploy/common.sh`** — `_wait_bg` function

Added a single line that prints what the script is waiting on before it starts waiting. This way the terminal immediately shows which background job is in progress rather than going silent.

```bash
# Before
_wait_bg() {
    local pid=$1 label=$2
    if wait "$pid"; then
        echo "✓ $label done"
    else
        echo "✗ $label FAILED"
        exit 1
    fi
}

# After
_wait_bg() {
    local pid=$1 label=$2
    # Print before waiting so the terminal doesn't appear frozen during long background jobs
    echo "Waiting for $label..."
    if wait "$pid"; then
        echo "✓ $label done"
    else
        echo "✗ $label FAILED"
        exit 1
    fi
}
```

With this change, the terminal now shows something like:

```
Migration job complete.
Waiting for Kafka deploy (Steps 2b3-2b4)...
✓ Kafka deploy (Steps 2b3-2b4) done
Waiting for MLflow deploy (Steps 2b5-2b6)...
✓ MLflow deploy (Steps 2b5-2b6) done
```

The same `_wait_bg` function is also used when waiting for the Airflow Docker build and the Flask deploy, so those waits now print a message too.

---

## Why this didn't stand out before

The background jobs (Kafka, MLflow, Flask, Airflow image build) were added during a parallelization pass to cut down total deploy time. The `_wait_bg` helper was written at the same time, and it was only ever tested in cases where those background jobs finished quickly — either because they were already cached or because they overlapped with a longer foreground step. In those cases the silence was brief enough that it did not register as a problem.

On a fresh spot instance, Kafka takes longer to pull its image and start up, so the gap between "Migration job complete." and the next line of output stretched to several minutes. That was long enough to make the deploy look stuck.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/common.sh` | `_wait_bg` now prints "Waiting for \<label\>..." before calling `wait`, so the terminal always shows which background job the script is waiting on |
