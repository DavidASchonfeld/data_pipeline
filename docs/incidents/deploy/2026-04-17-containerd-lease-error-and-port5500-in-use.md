# Deploy Warnings: Containerd Lease Error + MLflow Port Already in Use

**Date:** 2026-04-17
**Severity:** Low (deploy completed successfully; warnings printed but no step failed)
**Affected components:** `scripts/deploy/common.sh` — `import_image_to_k3s` helper; `scripts/deploy/mlflow.sh` — `step_mlflow_portforward`

---

## What was the problem

The deploy finished without error, but the warning summary at the end showed two entries:

```
> Error response from daemon: unable to lease content: lease does not exist: not found
> WARNING: port-forward may not have started. kubectl output from /tmp/mlflow-portforward.log:
> Unable to listen on port 5500: Listeners failed to create with the following errors:
>   [unable to create listener: Error listen tcp4 127.0.0.1:5500: bind: address already in use]
> error: unable to listen on any of the requested ports: [{5500 5500}]
```

These are two separate problems that happen to appear in the same deploy.

---

### Warning 1 — "lease does not exist: not found"

When an image is imported into K3S's container runtime (the software that runs containers
on the server), the runtime creates a short-lived internal record called a **lease** to
track the import while it is in progress. Once the import finishes, the lease is removed.

If a previous import was interrupted mid-way — for example, because the server ran out of
memory during one of the parallel background jobs — the lease is not removed cleanly. It
stays behind as a stale record in the container runtime's internal storage.

On the next deploy, when the import tries to create a *new* lease for the same content,
the runtime finds the old stale one and reports "lease does not exist: not found" —
a confusing error message that really means "the internal bookkeeping is in a bad state."
The retry logic in the import function recovered automatically, which is why the deploy
still completed, but the error was printed to the warning summary each time it happened.

---

### Warning 2 — "address already in use" on port 5500

After deploying MLflow, the script starts a **port-forward**: a background process that
creates a tunnel between port 5500 on the server and the MLflow service running inside
the cluster. This is what lets you reach the MLflow web interface from your laptop.

Before starting a new port-forward, the script kills any old one left over from the
previous deploy — both by process name and by port number. The problem is that after
sending the kill signal, the script waited only 1 second before trying to start the
new forward.

On this deploy, 1 second was not enough time for the operating system to fully release
the port. The new port-forward tried to bind to port 5500 and found it still occupied.
It printed the "address already in use" error and did not start, leaving the MLflow
web interface unreachable until a manual restart.

---

## What was changed

### `scripts/deploy/common.sh` — Clear stale containerd leases before each image import

One line was added inside the import retry loop, immediately before the `docker save`
command that feeds the image into K3S. It lists all leases currently held by the
container runtime and deletes them, so each import attempt starts with a clean slate.

```bash
# Before: import ran directly, failed if a stale lease was present
docker save '$image_name' | sudo k3s ctr images import -

# After: stale leases are cleared first so the import always starts clean
sudo k3s ctr leases ls -q 2>/dev/null | xargs -r sudo k3s ctr leases delete 2>/dev/null || true &&
docker save '$image_name' | sudo k3s ctr images import -
```

### `scripts/deploy/mlflow.sh` — Wait for port 5500 to be fully released before rebinding

The fixed `sleep 1` was replaced with a short loop that checks whether port 5500 is
still occupied after the kill signal. It waits up to 5 seconds, checking once per
second, and only continues once the port is confirmed free.

```bash
# Before: always waited exactly 1 second after kill, regardless of whether the port was released
fuser -k 5500/tcp >/dev/null 2>&1 || true
sleep 1

# After: waits up to 5s and proceeds as soon as the port is actually free
fuser -k 5500/tcp >/dev/null 2>&1 || true
for _wait in 1 2 3 4 5; do
    fuser 5500/tcp >/dev/null 2>&1 || break  # port is free — proceed
    sleep 1
done
```

---

## Why these didn't appear before

**Lease error:** Stale leases only accumulate after an interrupted import. Before the
parallel-jobs fix that ran three heavy background jobs at the same time, individual
imports rarely failed mid-way. Once parallel load made mid-import failures more common,
stale leases started appearing on the next deploy.

**Port in use:** On a warm server with a fast machine, the port releases well within
1 second after the kill. On a freshly started spot instance that is still under load
from the deploy, the OS takes a little longer to clean up the socket. 1 second was
enough on a fast server but not on a loaded one.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/common.sh` | Clear stale K3S containerd leases before each image import attempt |
| `scripts/deploy/mlflow.sh` | Replace `sleep 1` with a wait loop that confirms port 5500 is released before rebinding |
