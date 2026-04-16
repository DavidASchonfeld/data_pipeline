# Migration Job Timeout + Image Import Lease Error — April 16, 2026

**Date:** 2026-04-16
**Severity:** High (deploy fails at database migration step; Airflow cannot start with stale schema)
**Affected components:** `scripts/deploy/common.sh` (image import), `scripts/deploy/airflow_pods.sh` (migration job polling)

---

## What happened

A deploy failed with two errors in the warnings summary:

```
Error response from daemon: unable to lease content: lease does not exist: not found
ERROR: Migration job did not complete within 600s.
```

The first error appeared during the step that imports the Airflow Docker image into the
Kubernetes image store. The second error came 10 minutes later: the database migration
job (which upgrades Airflow's database schema after each deploy) never finished.

The deploy log showed the migration pod was created successfully but never completed —
not because it crashed, but because it appeared to be **hanging**: it ran for the full
10-minute window without printing a result.

---

## Why it happened

### Error 1: "unable to lease content: lease does not exist"

When a Docker image is imported into k3s (the Kubernetes runtime on EC2), the operation
uses a short-lived internal "lease" to track the in-progress transfer. The Airflow image
is large (about 3.3 GB), and when the server is under memory pressure from a recent
deploy, the import can take long enough that the internal tracking entry gets confused,
printing this error.

The previous version of the import step only retried twice with a 5-second pause —
not enough for a large image under load.

### Error 2: Migration job stuck for 600 seconds

After each Airflow upgrade, a short-lived job runs `airflow db migrate` to apply any
database schema changes. This job connects to the PostgreSQL database on the same server.

The deploy had been run three times in quick succession. After repeated deploys, PostgreSQL
can accumulate **stale database connections** from previous runs, or a previous interrupted
migration can leave behind a **database lock** (a reservation that prevents other operations
from modifying the same table). When this happens, the `airflow db migrate` command
connects to the database but then waits indefinitely for the lock to clear — it does not
crash, it does not print an error. It just hangs silently.

The previous detection logic only looked for the job reaching a "Completed" or "Failed"
state. Since the pod was neither (it was running and hanging), the deploy simply waited
the full 600 seconds and then gave up with a timeout error.

---

## What was changed to fix it

### 1. More resilient image import in `scripts/deploy/common.sh`

The `import_image_to_k3s` function now retries up to **5 times** (was 2) with a **15-second
pause** between each attempt (was 5 seconds). This gives the containerd image store enough
time to clean up the previous attempt before the next one starts.

### 2. Smarter migration job polling in `scripts/deploy/airflow_pods.sh`

Three improvements were made to the migration job wait:

**Extended timeout to 900 seconds (was 600s)**
This provides a wider safety window and ensures the deploy does not give up right before
the job would have finished or failed on its own.

**Restart the pod if it has been running for 6 minutes without completing**
If the migration pod is in the "Running" state for more than 360 seconds with no result,
the deploy now force-restarts the pod. This clears any stale database connections or
locks held by the previous attempt, allowing the new pod to connect cleanly and complete
the migration.

**Detect and recover from image-related errors immediately**
If the migration pod is stuck because the image could not be pulled (for example, if the
image import failed earlier), the deploy now detects this within seconds and immediately
recreates the migration job, rather than waiting the full 900 seconds.

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy/common.sh` | `import_image_to_k3s`: 2 retries → 5, 5s delay → 15s |
| `scripts/deploy/airflow_pods.sh` | Migration polling: 600s → 900s; pod-hang restart at 360s; CrashLoopBackOff fast-fail |

---

## How to avoid this in the future

On subsequent deploys, if the image import has a transient error, the retry logic will
handle it automatically. If the migration pod hangs, it will be restarted automatically
at the 6-minute mark. In most cases the deploy will recover and complete without any
manual action needed.

If the migration job fails even after all retries, the deploy will stop and print the
last 30 lines of the migration pod's logs. This output will usually show the exact
database error (lock timeout, connection refused, etc.) that caused the hang, making it
much easier to diagnose manually.
