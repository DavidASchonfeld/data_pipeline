# Incident: dag-processor "still 2 pods after 5 min" warning

**Date:** 2026-04-16  
**Component:** Deploy script — Airflow pod restart  
**Severity:** Warning only — deploy completed successfully

---

## What Happened

After a deployment, the final summary showed this warning:

```
WARNING: still 2 dag-processor pods after 5 min — old RS may not have scaled down
```

The deploy itself finished without error. Airflow was running normally afterwards.

---

## Plain-English Explanation

When the deploy updates Airflow, Kubernetes does a "rolling update" on the dag-processor — the process that reads and schedules your DAG files. The rolling update works like this:

1. Start a new copy of the dag-processor with the updated code
2. Once the new copy is healthy, shut down the old copy

During step 2, there is briefly a moment where both the old and new copies exist at the same time (2 pods). Kubernetes normally cleans up the old copy within 30–60 seconds.

The deploy script waits up to 5 minutes for the old copy to disappear. In this case, it didn't disappear in time — the rolling update was still in progress when the 5-minute limit expired.

**Why the rolling update was slow:** Running multiple rapid back-to-back deploys (as happened on April 16) puts extra load on the cluster. The rolling update controller took longer than usual to finish swapping the pods.

---

## What the Script Did

When the 5-minute wait expired, the script automatically tried to recover:

1. **Force-told Kubernetes** to stop maintaining the old dag-processor copy (scaled its "ReplicaSet" to zero replicas)
2. **Waited up to 60 more seconds** for the old pod to finish shutting down gracefully
3. If it was still there after that, the script issued the warning and moved on

Either way, the script then deleted and re-created all Airflow pods in the normal Phase A step — so the warning did not affect the outcome of the deploy.

---

## Root Cause

Two things compounded:

1. The pre-check that waits for the rolling update to finish (`kubectl rollout status --timeout=300s`) timed out silently after 5 minutes, without the old ReplicaSet having scaled to 0. The script continued anyway (the timeout is non-fatal by design, because a fresh server has no rollout in progress at all).

2. The subsequent 5-minute poll also expired, leaving the old pod still running. The force-scale that followed only slept 5 seconds before continuing — not enough time for the old pod to actually finish shutting down before the warning was printed.

---

## Fix Applied

**File:** `scripts/deploy/airflow_pods.sh`

Changed the timeout-recovery block so that:

- The force-scale happens first (same as before)
- A **secondary wait loop** (up to 60 seconds, checking every 5 seconds) then waits for the old pod to actually terminate — Kubernetes' default graceful shutdown period is 30 seconds, so 60 seconds is enough in nearly all cases
- The `WARNING` keyword is only printed if the pod is **still present after both the force-scale and the secondary wait** — meaning auto-recovery genuinely failed and human review is needed
- If the force-scale resolves it cleanly, a plain informational message is printed instead (which does not surface in the deploy summary)

**Net effect:** On normal rapid-redeploy scenarios, the warning will no longer appear. It is now reserved for cases where the script truly cannot clear the stuck pod, so when it does appear it is actionable.

---

## When to Investigate Further

If you still see this warning after the fix, it means the pod is stuck in a way the auto-recovery could not clear. Check:

```bash
# SSH to EC2, then:
kubectl get pods -l component=dag-processor -n airflow-my-namespace
kubectl describe pod -l component=dag-processor -n airflow-my-namespace | tail -30
```

Common causes of a genuinely stuck pod:
- A Kubernetes finalizer preventing deletion (rare)
- Very high cluster memory pressure causing slow container shutdown
- A bug in the DAG code causing the process inside the pod to hang during shutdown
