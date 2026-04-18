# MLflow Deploy Failed: Docker Layer Extraction Error + kubectl "permission denied"

**Date:** 2026-04-17
**Severity:** High (MLflow never deployed; deploy terminated)
**Affected components:** `scripts/deploy/mlflow.sh` — Step 2b5a (docker pull) and Step 2b6 (kubectl commands)

---

## What was the problem

The deploy failed at the MLflow step with two separate but related errors, both caused by the
same root cause: three heavy jobs running at the same time on a small server.

### Error 1 — Docker could not download the MLflow image

```
failed to extract layer ... to overlayfs ...: mount callback failed ...:
failed to Lchown ".../lib/aarch64-linux-gnu/libsepol.so.1" for UID 0, GID 0:
lchown ...: no such file or directory

✗ MLflow deploy (Steps 2b5-2b6) FAILED
```

When Docker downloads an image, it downloads each piece (called a "layer") separately and
then unpacks them in order — each layer builds on top of the previous one. The error above
means Docker tried to set ownership on a file in layer N+1, but the file from layer N that
it was supposed to sit on top of was missing.

This is a corruption problem: the file system got into an inconsistent state because the
Airflow build job and the MLflow download job were both writing to Docker's internal storage
at exactly the same time on a 2 GB server.

### Error 2 — kubectl could not read its configuration file (from prior run)

```
error: error loading config file "/etc/rancher/k3s/k3s.yaml": permission denied
```

`kubectl` — the tool that sends instructions to the cluster — needs a configuration file
called `/etc/rancher/k3s/k3s.yaml` to connect. This file is normally locked to the system
administrator. The deploy script unlocks it at the start of every deploy (Step 1c), but K3s
resets it back to locked whenever K3s itself restarts. Under the heavy parallel load on a
fresh instance, K3s restarted mid-deploy, and the file was locked again before MLflow's
background job got to its cluster commands.

---

## Why both errors happen on the same deploy

Both errors come from the same situation: a fresh spot instance with cold (empty) caches
running three heavy background jobs simultaneously. On a warm instance, the images are
already cached so the jobs finish quickly and barely overlap. On a cold instance, all three
jobs are downloading and writing large files at the same time, which overwhelms the server's
memory and I/O — causing Docker's overlay filesystem to corrupt and K3s to restart.

---

## What was changed

### Fix 1 — `scripts/deploy/mlflow.sh` — Retry the Docker pull on layer extraction failure

Before this fix, if `docker pull` failed for any reason, the deploy stopped immediately.
Now it retries up to 3 times. Between each failed attempt, the partial/corrupted image is
deleted so Docker downloads everything completely fresh on the next try.

```bash
# Before: one attempt, instant failure on any error
docker pull ghcr.io/mlflow/mlflow:latest

# After: up to 3 attempts; removes corrupted partial image between failures
for _pull_attempt in 1 2 3; do
    if docker pull ghcr.io/mlflow/mlflow:latest; then
        break  # pull succeeded — exit retry loop
    fi
    if [ "$_pull_attempt" -lt 3 ]; then
        echo "Pull attempt $_pull_attempt failed — removing partial image and retrying in 15s..."
        docker rmi ghcr.io/mlflow/mlflow:latest 2>/dev/null || true
        sleep 15
    else
        echo "✗ MLflow docker pull failed after 3 attempts"
        return 1
    fi
done
```

### Fix 2 — `scripts/deploy/common.sh` — New `_ensure_kubectl_accessible` helper

A small reusable helper was added that re-unlocks the K3s configuration file so `kubectl`
can read it. Unlike the one-time unlock in `setup.sh`, this can be called right before
any `kubectl` command in any background job.

```bash
# Re-applies read permission on the K3s config file — K3s resets it to root-only whenever
# the k3s service restarts (e.g., due to memory/IO pressure during heavy parallel builds).
_ensure_kubectl_accessible() {
    ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml 2>/dev/null || true"
}
```

### Fix 3 — `scripts/deploy/mlflow.sh` — Call the helper before Step 2b6

One line was added just before the first `kubectl` command in the MLflow deploy step:

```bash
# Re-apply kubectl permissions — K3s resets k3s.yaml to root-only if it restarts
# during the parallel image import above, causing "permission denied" failures
_ensure_kubectl_accessible
```

### Fix 4 — `scripts/deploy/kafka.sh` — Same `_ensure_kubectl_accessible` call

The same one-line call was added before Kafka's first `kubectl` command. Kafka runs in
the same parallel phase and is equally vulnerable to the permissions reset.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/mlflow.sh` | Retry `docker pull` up to 3 times with corrupted image removal between attempts |
| `scripts/deploy/common.sh` | Added `_ensure_kubectl_accessible` helper that re-applies the K3s config file permission |
| `scripts/deploy/mlflow.sh` | Calls `_ensure_kubectl_accessible` before the first kubectl command in Step 2b6 |
| `scripts/deploy/kafka.sh` | Calls `_ensure_kubectl_accessible` before the first kubectl command in Step 2b4 |
