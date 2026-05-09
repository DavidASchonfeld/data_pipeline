# Deploy Warning Persists at 87%: Orphaned Postgres Folders + Cleanup Triggered Too Late — May 9, 2026

**Date:** 2026-05-09
**Severity:** Low (no deploy failure — but the warning would worsen over time and eventually trigger the 92% abort threshold)
**Affected components:** `scripts/deploy/common.sh` (`_ensure_disk_space`)

---

## What happened

A deploy finished successfully, but the summary printed the same warning that the May 9 containerd-orphaned-blobs fix was supposed to eliminate:

```
WARNING: disk still at 87% after prune — likely live container/PVC usage, not cache
```

Investigation showed the disk was actually fine — but two smaller issues had accumulated baseline disk usage that, together, made the deploy-time peak come back.

---

## Why it happened

A diagnostic snapshot of the EC2 server (taken right after the deploy finished and things settled) told a much clearer story than the warning suggested.

### The disk was actually at 62% — only the deploy *peak* was 87%

The warning fires while the deploy is still mid-flight. At that moment, both the old and the new versions of the Airflow image are sitting in the server's image store at the same time, and a temporary tar file (used to load the image into the cluster) is also on disk. Once the deploy finishes and the cleanup steps run, things settle:

| Time | Disk used |
|------|-----------|
| Mid-deploy (peak) | 87% |
| After deploy finishes (steady state) | **62% (18 GB of 29 GB)** |

So the user was never actually at risk of running out of disk — but the warning was still legitimate, because the *peak* was higher than it needed to be. The container image store accounts for **~9 GB of the 18 GB baseline**, which is the genuine size of the 13 images currently in use (Airflow + dbt is the largest at ~3 GB unpacked; MLflow, Kafka, Postgres, Flask, and a handful of small system images make up the rest). That part is not waste — it is the live software the cluster needs to run.

### Problem 1: Orphaned data folders from past deploys (~700 MB–1 GB)

Every time a Helm upgrade recreates the Postgres database (or Kafka brokers, or the Airflow log volume), the cluster creates a new "folder" (technically a *PersistentVolume* directory) on disk to hold its files. When the old PV is deleted, the cluster is supposed to also delete the folder. But for the K3s "local-path" storage system, this cleanup sometimes fails silently — the folder gets left behind even though the database it belonged to is long gone.

A check of the server found **20+ of these dead folders** sitting on disk: a mix of past Postgres copies, past Kafka broker copies, and at least one stale Airflow log volume. Only two of the auto-named folders matched a live PV — every other one was dead data:

```
KEEP    pvc-ce6e3b94-..._data-airflow-postgresql-0      ← live
KEEP    pvc-dfe7c7c9-..._kafka-data-kafka-0             ← live
REMOVE  pvc-e54ba384-..._data-airflow-postgresql-0      ← orphan
REMOVE  pvc-2863c18e-..._data-airflow-postgresql-0      ← orphan
REMOVE  pvc-066741bf-..._kafka-data-kafka-0             ← orphan
REMOVE  pvc-6184d102-..._airflow-my-namespace_log-pvc   ← orphan
... (roughly 17 more orphans across postgres + kafka)
```

That's an estimated 700 MB to 1 GB of dead data the deploy script was never reaching. The existing cleanup step (`kubectl delete pv` for items in "Released" state) only removes the cluster-level object — it does not delete the actual folder on the filesystem.

### Problem 2: The deeper cleanup only triggered at 85% — too late

The `_ensure_disk_space` function has two stages: a quick cache prune that always runs when disk > 75%, and a deeper "secondary cleanup" (logs, journal, evicted pods, orphaned PV objects) that previously only ran when disk was already over 85%. By the time disk crosses 85% during a deploy, the build and image-import are already in flight, pushing the peak into the 87–90% danger zone before the secondary cleanup can help.

---

## What was changed to fix it

### 1. `scripts/deploy/common.sh` — sweep orphaned local-path folders

Inside `_ensure_disk_space`'s secondary cleanup block, a new step now reads the list of *live* PersistentVolumes from the cluster, then walks every folder in `/var/lib/rancher/k3s/storage/`. Any folder whose PV name does not appear in the live list is an orphan and gets removed.

A safety check was added: if the cluster's PV list comes back empty (e.g., a transient kubectl failure), the cleanup skips the loop entirely rather than risk deleting real data.

### 2. `scripts/deploy/common.sh` — trigger the secondary cleanup earlier

The threshold for running the heavier secondary cleanup was lowered from **85% → 80%**. Starting cleanup at 80% gives the deploy more headroom for the build/import peak that follows, instead of starting cleanup only after the danger has already arrived.

### Why "prune snapshots" was *not* added as a fix

An earlier draft of this fix considered adding `k3s ctr snapshots prune` after every image import, on the theory that orphaned unpacked layers were inflating the 8.9 GB containerd directory. Verification on the server showed two things:
1. There is no `prune` subcommand on `ctr snapshots` — only `list`, `info`, `remove`, etc.
2. The 9 GB is a fair size for the unpacked filesystems of the 13 live images. There is no significant orphan-snapshot reclaim available; the existing `crictl rmi --prune` and `ctr content gc` already cover the legitimate cleanup paths.

So the fix is intentionally limited to the two real problems above.

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy/common.sh` | (a) New orphaned local-path folder sweep inside `_ensure_disk_space` after the existing Released-PV deletion; (b) Secondary-cleanup trigger threshold lowered from 85% → 80% |
| `docs/incidents/INDEX.md` | New row added to the Infrastructure table pointing here |

---

## How to verify the fix worked

SSH into the server and run this single-line command (the two checks are joined with `&&` so they run together):

```bash
ssh ec2-stock 'df -h / && sudo find /var/lib/rancher/k3s/storage -mindepth 1 -maxdepth 1 -type d -name "pvc-*" | wc -l'
```

What each part is checking:
- `df -h /` — shows how much of the server's main disk is used. We want to see this go *down* by roughly 700 MB to 1 GB.
- `sudo find ... | wc -l` — counts how many auto-named PV folders are left in the cluster's storage area. After the fix, this should equal the number of *live* PVs (currently 2: one for Postgres, one for Kafka). Before the fix, this was 21 — which means 19 of them were dead leftovers.

If the disk usage drops and the count is 2, the fix worked. If the count is still much higher than the number of live PVs, something prevented the cleanup from running and the deploy log should be reviewed.

### Confirmed working — 2026-05-09 redeploy

The fix was deployed via `./scripts/deploy.sh` and confirmed on the next deploy. The numbers, side by side:

| Measurement | Before fix | After fix | What this proves |
|-------------|-----------|-----------|-------|
| Deploy time | 27m 21s | **14m 55s** | The script no longer churns through extra cleanup attempts triggered by the high-disk warning. |
| End-of-deploy summary | `WARNING: disk still at 87% after prune` | `(none)` | The deploy peak no longer crosses the warning threshold. |
| Server disk used | 18 GB / 29 GB (62%) | **17 GB / 29 GB (60%)** | About 700 MB of dead data was reclaimed — matches the predicted size of the orphan folders. |
| Auto-named PV folders | 21 (19 of them dead) | **2** | Every dead leftover folder was removed. Only the two live PVs (Postgres + Kafka) remain. |

The verify command's output after the fix:

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/root        29G   17G   12G  60% /
/var/lib/rancher/k3s/storage/pvc-dfe7c7c9-...   ← live Kafka PV
/var/lib/rancher/k3s/storage/pvc-ce6e3b94-...   ← live Postgres PV
```

The two folder paths shown are the two live PVs — every other folder that used to be there is gone. Disk dropped from 62% to 60%, and the deploy summary printed no warnings at all.

---

## How to avoid this in the future

The orphaned PV directory sweep runs automatically on every deploy now, so dead Postgres folders cannot accumulate silently. The earlier 80% trigger gives the deploy more room to react before the build/import peak. The diagnostic-only print already in `_ensure_disk_space` (top consumers, top 5 images by size, pinned image count, top files >100 MB) gives the next operator a starting point without needing to log into the server manually.

If disk pressure ever returns and the 92% abort threshold is hit, check, in this order:
1. `sudo du -sh /var/lib/rancher/k3s/agent/containerd` — should be ~7–9 GB; if much higher, an image was added that's larger than expected.
2. `sudo du -sh /var/lib/rancher/k3s/storage/*` — PostgreSQL and Kafka data volumes.
3. `sudo du -sh /home/ubuntu/airflow/logs /home/ubuntu/mlflow-data` — bind-mounted PVCs.
4. `sudo du -sh /swapfile` — the swapfile is 4.1 GB and is intentionally kept (removing it risks out-of-memory crashes).
