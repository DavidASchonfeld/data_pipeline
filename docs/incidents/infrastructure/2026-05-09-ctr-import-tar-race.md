# Deploy Fails: `ctr: open /tmp/k3s-import-*.tar: no such file or directory` — May 9, 2026

**Date:** 2026-05-09
**Severity:** High (deploy hard-fails; all background jobs killed by EXIT trap)
**Affected components:** `scripts/deploy/common.sh` (`_ensure_disk_space`, `import_image_to_k3s`)

---

## What happened

Deploy failed at Step 2b6 / Step 7a with no WARNING/ERROR keywords in the log — only the last 15 lines were shown, ending with:

```
#11 exporting to image
#11 exporting layers
ctr: open /tmp/k3s-import-78ZCJ2.tar: no such file or directory
```

The Flask build (`#11`) was mid-export when the deploy aborted. The `ctr` error came from a different parallel SSH session. Exit code 1 caused the EXIT trap to kill all background jobs, which is why the Flask build has no `DONE` line.

---

## Why it happened

Three places in the deploy scripts create a temp tar to import a Docker image into K3s containerd:

| Location | Pattern |
|----------|---------|
| `common.sh` — `import_image_to_k3s` (used for airflow-dbt and MLflow) | `mktemp /tmp/k3s-import-XXXXXX.tar` |
| `mlflow.sh` — ErrImageNeverPull recovery inside polling loop | same pattern |
| `airflow_pods.sh` — `step_verify_airflow_image` + pod re-import fallback | same pattern |

Two cleanup paths deleted the entire pattern with a wildcard:

- **`common.sh:186`** — `sudo rm -f /tmp/k3s-import-*.tar` — inside `_ensure_disk_space` secondary cleanup (runs when disk > 85%)
- **`common.sh:322`** — `rm -f /tmp/k3s-import-*.tar` — inside `import_image_to_k3s` retry block

The deploy runs Kafka, MLflow, and Flask as parallel background jobs while `step_helm_upgrade` runs in the foreground. In Phase 4:

1. `step_helm_upgrade` called `step_verify_airflow_image`, which found the airflow image missing and started an inline import: `mktemp → docker save → ctr import`.
2. Simultaneously, the MLflow polling loop (still executing in the background) detected disk pressure (89%→90%) and called `_ensure_disk_space`, which triggered secondary cleanup — including `sudo rm -f /tmp/k3s-import-*.tar`.
3. The wildcard `rm` unlinked the tar path mid-flight. `docker save` continued writing through its open file handle (the inode survives until the handle is closed), but when `ctr images import` then tried to open the path by name, the name was gone — `ENOENT`.

---

## What was changed to fix it

Both wildcard cleanup lines were replaced with `find ... -mmin +5 -delete`. This means:

- Tars younger than 5 minutes (any active import) are left alone.
- Tars older than 5 minutes (orphaned from a crashed prior run) are still swept as a backstop.

| File | Old | New |
|------|-----|-----|
| `common.sh:186` | `sudo rm -f /tmp/k3s-import-*.tar` | `sudo find /tmp -maxdepth 1 -name 'k3s-import-*.tar' -mmin +5 -delete` |
| `common.sh:322` | `rm -f /tmp/k3s-import-*.tar` | `find /tmp -maxdepth 1 -name 'k3s-import-*.tar' -mmin +5 -delete` |

---

## How to verify the fix worked

Run a full deploy and confirm:

1. Step 2b6 (MLflow rollout) and Step 7a (`step_verify_airflow_image`) both complete without `ctr: open ... no such file or directory`.
2. After the deploy: `ssh ec2-stock 'ls /tmp/k3s-import-*.tar 2>/dev/null'` returns nothing — per-import `rm -f "$_tmp"` cleaned up normally.

---

## Why this surfaced now

The race existed before but was rare — `_ensure_disk_space` secondary cleanup only fires when disk > 85%. The May 9 containerd-orphaned-blobs fix (separate incident, same day) added more `k3s ctr content gc` calls which briefly hold containerd leases during cleanup, slightly extending the window when disk is still > 85%. That, combined with the repeated MLflow Evicted pods calling `_ensure_disk_space` multiple times in the polling loop, made the race probable on this deploy.
