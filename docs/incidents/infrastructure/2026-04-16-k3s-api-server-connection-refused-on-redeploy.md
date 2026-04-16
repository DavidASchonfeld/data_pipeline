# K3s API Server "Connection Refused" on Rapid Redeploy — April 16, 2026

**Date:** 2026-04-16
**Severity:** High (deploy fails mid-way; all secret and manifest application is blocked)
**Affected components:** `scripts/deploy/sync.sh` — Step 2c (K8s secret and manifest application)

---

## What happened

Running `./scripts/deploy.sh` a second time shortly after the first one completed caused the
deploy to fail partway through with this error:

```
error validating "/home/ubuntu/airflow/manifests/snowflake-secret.yaml":
failed to download openapi: Get "https://127.0.0.1:6443/openapi/v2?timeout=32s":
dial tcp 127.0.0.1:6443: connect: connection refused
```

Port 6443 is the k3s control center — it handles all Kubernetes commands on the EC2 server.
The deploy failed at the step where it applies the Snowflake credentials to the cluster.
No changes were made to the cluster; re-running the deploy would eventually succeed.

---

## Why it happened

Two things combined to cause this.

**1. The k3s API server briefly restarts under resource pressure.**

A full deploy is resource-intensive: it builds Docker images, starts a Helm upgrade, and
creates or replaces multiple pods all at once. On a spot instance with limited memory, k3s
can briefly restart its control center in the middle of this. When that happens, port 6443
stops accepting connections for a short window — usually under a minute.

Running a second deploy so soon after the first means k3s is still under that resource
pressure, and the restart can happen right when the deploy needs it most.

**2. The existing readiness check runs too early and tests the wrong thing.**

The deploy script checks whether k3s is ready once, near the very beginning — before any
of the heavy work starts. By the time Step 2c runs (often 5–10 minutes later), that check
is no longer meaningful.

More importantly, the existing check asks: *"can kubectl list the Kubernetes nodes?"*
That question is answered by a basic part of the k3s API, which starts up early.
But `kubectl apply` needs a different part — the OpenAPI schema endpoint — which starts
up *later*. So it is possible for the node-listing check to pass while `kubectl apply`
is still unable to connect.

---

## What was changed to fix it

This required two rounds of changes as the problem turned out to be more serious than
a brief restart — k3s was stuck in a crashed state and would not recover on its own.

### Round 1 (initial fix): Added `_wait_k3s_api_ready()` to `scripts/deploy/common.sh`

A new function was added that checks the k3s API server's `/healthz` endpoint directly.
This endpoint only returns `ok` when the API server is fully initialized — including the
part that `kubectl apply` depends on. It was called at the top of `step_sync_manifests_secrets()`
in `sync.sh`, right before the first `kubectl apply`.

This fixed the "connection refused" error from the first incident, but a second deploy
exposed a deeper problem: k3s could be stuck in a crashed state for more than 5 minutes,
causing the wait function itself to time out.

### Round 2 (recovery fix): Active k3s restart + switch to kubectl-native health check

Two improvements were made to `_wait_k3s_api_ready()`:

**1. Active restart after 50 seconds of unresponsiveness**

Instead of passively waiting for k3s to come back on its own, the function now restarts
the k3s systemd service after 5 failed attempts (~50 seconds). This breaks out of any
stuck or crash-loop state automatically, without needing manual intervention.

**2. Switched from `curl` to `kubectl get --raw /healthz`**

The original version used `curl -sk https://localhost:6443/healthz`. The replacement
uses `kubectl get --raw /healthz`, which uses k3s's own kubeconfig and certificates.
This is more reliable because it does not depend on `curl` being installed, and it
uses the same connection path that `kubectl apply` itself uses.

```bash
# kubectl apply fetches /openapi/v2 before doing anything — that endpoint
# initializes later than kubectl get nodes, so we verify it explicitly.
_wait_k3s_api_ready() {
    echo "=== Waiting for K3s API server to be ready (up to 6 minutes) ==="
    # 36 attempts × 10s = 6 min; uses kubectl's own kubeconfig so no curl/TLS issues
    for _attempt in $(seq 1 36); do
        if ssh "$EC2_HOST" "kubectl get --raw /healthz 2>/dev/null | grep -q 'ok'"; then
            echo "✓ K3s API server ready (attempt $_attempt)"
            return 0
        fi
        # After 5 failed attempts (~50s), actively restart k3s to break out of a stuck/crashed state
        if [ "$_attempt" -eq 5 ]; then
            echo "K3s API unresponsive after 50s — restarting k3s service to recover..."
            ssh "$EC2_HOST" "sudo systemctl restart k3s" || true
            echo "K3s restarted — waiting for it to come back up..."
        fi
        echo "K3s API not ready yet (attempt $_attempt/36), retrying in 10s..."
        [ "$_attempt" -lt 36 ] && sleep 10
    done
    echo "✗ K3s API server did not become ready after 6 minutes"
    return 1
}
```

### Called at the top of `step_sync_manifests_secrets()` in `scripts/deploy/sync.sh`

The check runs at the start of the function where the original failure occurred —
immediately before any `kubectl apply` command is attempted.

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy/common.sh` | Added `_wait_k3s_api_ready()` with active restart + kubectl-native health check |
| `scripts/deploy/sync.sh` | Called `_wait_k3s_api_ready()` at the top of `step_sync_manifests_secrets()` |

---

## How to avoid this in the future

This fix is self-healing: if the k3s API server is slow to respond, the deploy pauses and
retries automatically. If it is completely unresponsive for 50 seconds, the deploy restarts
the k3s service and waits for it to recover — all without any manual intervention.

If k3s cannot recover after 6 minutes even with a restart, the deploy stops with a clear
error so the operator can SSH in and investigate directly (e.g., check disk space, memory,
or systemd journal logs with `sudo journalctl -u k3s -n 50`).
