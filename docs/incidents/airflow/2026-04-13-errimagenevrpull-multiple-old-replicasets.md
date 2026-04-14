# ErrImageNeverPull: Multiple Old ReplicaSets After Spot Instance Redeploy

**Date:** 2026-04-13
**Severity:** High (deploy failed, all Airflow pods stuck)
**Affected component:** `scripts/deploy/airflow_pods.sh` — Step 7 pod restart

---

## What was the problem

When `./scripts/deploy.sh --provision --snowflake-setup` ran on a freshly provisioned spot instance, Step 7 (restarting the Airflow pods) failed with this error:

```
ErrImageNeverPull: Container image "airflow-dbt:3.1.8-dbt-20260413191233" is not present with pull policy of Never
```

Here is what happened, step by step:

Every time you deploy, Kubernetes creates a fresh copy of the "dag-processor" component using the newly built image. The old copy is kept around briefly so Kubernetes can switch traffic over smoothly, then discarded. Over time — especially after multiple deploys on a new spot instance that inherits Kubernetes state — several of these old copies (called ReplicaSets) can pile up. In this case there were three:

1. The **oldest** copy — from a much earlier deploy, already marked for deletion
2. A **middle** copy — from the previous deploy, still set to keep 1 pod running, and referencing an image (`*-20260413191233`) that had already been deleted from the server during the current build
3. The **newest** copy — from the current deploy, with the correct fresh image

The deploy script already had logic to handle this situation, but it only ever shut down **one** old copy (the oldest). The middle copy was left alone, still configured to keep a pod alive. When the script deleted all the dag-processor pods to force a clean restart, the middle copy immediately tried to recreate its pod using its image — which no longer existed on the server. Since the server is configured to never download images from the internet (everything must be pre-loaded), the pod got permanently stuck with `ErrImageNeverPull`.

This failure then cascaded into the triggerer pod. The triggerer waits for the database migration step to complete before starting up. Because the scheduler and dag-processor were stuck, migrations never finished in time, and the triggerer timed out after 10 minutes.

---

## What was changed

**`scripts/deploy/airflow_pods.sh`**

The force-shutdown logic was changed from "shut down the single oldest copy" to "shut down every old copy except the newest one."

Before, it found only the first (oldest) copy:
```bash
OLD_RS=$(kubectl get rs ... -o jsonpath='{.items[0].metadata.name}')
kubectl scale rs "$OLD_RS" --replicas=0
```

After, it gets the full list sorted oldest-to-newest, then shuts down everything except the last one:
```bash
RS_LIST=$(kubectl get rs ... --sort-by=.metadata.creationTimestamp ...)
TOTAL=$(echo "$RS_LIST" | grep -c .)
if [ "$TOTAL" -gt 1 ]; then
    echo "$RS_LIST" | head -n $(( TOTAL - 1 )) | while read -r OLD_RS; do
        kubectl scale rs "$OLD_RS" --replicas=0
    done
fi
```

This is safe regardless of how many old copies exist — it always leaves the newest one (which has the current image) untouched, and zeros out everything else before deleting pods.

---

## Why this didn't happen before

On a long-running server, Kubernetes gradually cleans up old ReplicaSets automatically as part of normal rolling updates. After a typical deploy, there is only one old copy that needs cleaning up, and the existing logic handled that fine.

On a freshly provisioned spot instance, multiple rounds of "provision then deploy" can happen in quick succession during testing and debugging. Each round leaves behind an additional old ReplicaSet. After enough rounds, there were two stale copies instead of one, and the existing code only handled one.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_pods.sh` | Force-scale loop now shuts down ALL old ReplicaSets, not just the oldest one |
