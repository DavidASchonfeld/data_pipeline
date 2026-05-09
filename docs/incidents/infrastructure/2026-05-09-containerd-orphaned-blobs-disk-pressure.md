# Deploy Warnings: Disk Stays at 87–90% Even After Cleanup — May 9, 2026

**Date:** 2026-05-09
**Severity:** Medium (deploy completes but with warnings; left unaddressed, disk fills up over time and triggers the kubelet eviction failures seen on May 8)
**Affected components:** `scripts/deploy/airflow_image.sh`, `scripts/deploy/common.sh` (`_ensure_disk_space`)

---

## What happened

A deploy finished successfully but printed two warnings in the summary:

```
WARNING: disk still at 90% after prune — likely live container/PVC usage, not cache
WARNING: disk still at 87% after prune — likely live container/PVC usage, not cache
```

The deploy script already runs a cleanup step (`_ensure_disk_space`) to clear temporary files before the disk gets too full. After that cleanup, the disk was still at 87–90% — which the script flagged as a warning because it could not figure out what was still using the space.

---

## Why it happened

The root cause was found by checking the server directly:

- **The disk had 18 GB in use** (62%) at the start of the deploy — normal.
- **The disk spike to 90% happened during the build**, when the new application image was being copied into the server's internal image store.
- **After the cleanup, it settled at 87%** — still high, because the cleanup only cleared Docker's temporary files. It did not clear the thing that was actually taking up space.

### The culprit: leftover image layers from past builds (6.9 GB of "ghost data")

Every deploy builds a fresh copy of the Airflow application image and loads it into K3s (the program that runs the server's containers). Before loading the new copy, the deploy removes the *name tag* of the old image — but this is only like removing a label from a box. The box (the actual image layers taking up gigabytes of disk) stays behind.

K3s has a built-in garbage collector that's supposed to clean up these unlabelled boxes automatically, but it only runs on its own schedule — not immediately after every deploy. So over 6+ deploys, the leftover boxes stacked up:

| What's on disk | Size |
|----------------|------|
| Images actually in use (all 13 of them) | ~2.1 GB |
| "Ghost" layers from old builds, never cleaned up | **~6.9 GB** |
| **Total K3s image store** | **9.0 GB** |

The cleanup step the deploy already had (`crictl rmi --prune`) only removes unused *labels* — it also does not clean up the orphaned layer data. So neither the existing cleanup nor K3s's automatic schedule happened to run before the deploy, and 6.9 GB of invisible dead weight stayed on disk.

The disk breakdown from the server at the time:

| Item | Size |
|------|------|
| K3s image store (incl. ~6.9 GB ghost layers) | 9.0 GB |
| Swapfile (used as extra memory) | 4.1 GB |
| Everything else (OS, logs, databases, etc.) | ~5 GB |
| **Total used** | **~18 GB of 29 GB** |

---

## What was changed to fix it

### 1. `scripts/deploy/airflow_image.sh` — run the garbage collector right after every image import

After the new image is loaded into K3s, the deploy now immediately runs K3s's garbage collector:

```
sudo k3s ctr content gc
```

This tells K3s "clean up any layer data that no image label is currently pointing to." Since the old label was already removed before the new image was loaded, this one command reclaims all the ghost layers — about 6.9 GB on the first run, and roughly 1.2 GB on every subsequent deploy.

### 2. `scripts/deploy/common.sh` — also run the garbage collector in the general disk-cleanup step

The general cleanup function (`_ensure_disk_space`) already ran `crictl rmi --prune` to remove unused image labels. Now it also runs `k3s ctr content gc` right after, as a backstop. This catches any ghost layers that might accumulate from images other than the main Airflow image (e.g., MLflow, Flask).

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy/airflow_image.sh` | Added `sudo k3s ctr content gc` immediately after every new image import |
| `scripts/deploy/common.sh` | Added `sudo k3s ctr content gc` to the `_ensure_disk_space` cleanup pipeline, right after `crictl rmi --prune` |

---

## How to verify the fix worked

SSH into the server and run:

```bash
sudo k3s ctr content gc
sudo du -sh /var/lib/rancher/k3s/agent/containerd
```

The containerd store should drop from **9.0 GB → ~2–3 GB** immediately. On the next deploy, it should stay near that level instead of climbing back up.

---

## How to avoid this in the future

The garbage collector now runs automatically on every deploy, so the ghost layers never get a chance to accumulate. If the disk warnings ever reappear after this fix, the cause is something else (databases growing, log files accumulating, etc.) — not orphaned image layers.

If disk pressure ever returns and the 92% abort threshold is hit, check:
1. `sudo du -sh /var/lib/rancher/k3s/agent/containerd` — should be ~2–3 GB after a recent deploy
2. `sudo du -sh /var/lib/rancher/k3s/storage/*` — PostgreSQL and Kafka data volumes
3. `sudo du -sh /swapfile` — the swapfile is 4.1 GB and is intentionally kept (removing it risks out-of-memory crashes)
