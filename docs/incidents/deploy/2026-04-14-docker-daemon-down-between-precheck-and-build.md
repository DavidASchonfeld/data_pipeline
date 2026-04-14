# Docker Build Failed — Daemon Went Down After Pre-Flight Check Passed

**Date:** 2026-04-14
**Severity:** High (Step 2b2 failed; Airflow image never built; all downstream steps terminated)
**Affected component:** `scripts/deploy/airflow_image.sh`, `scripts/deploy/common.sh`

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy failed at the Docker build step with:

```
ERROR: Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?
✗ Airflow Docker build + K3S import (Step 2b2) FAILED
```

This is the same error message as the earlier "Docker Daemon Not Running" incident, which had already been fixed by adding a pre-flight check (Step 1c3). The pre-flight check passed this time — Docker was confirmed running — but by the time the actual build started a short while later, the Docker service had gone down.

Here is what happened, step by step:

1. The deploy ran the pre-flight checks in Phase 1, including Step 1c3 which runs `docker info` to verify that the Docker service is alive and responding. The check passed — Docker was running.

2. The deploy then moved on to syncing DAG files and the Dockerfile to EC2 (Steps 2 and 2b1). These steps took about 15–30 seconds.

3. In Phase 2, the deploy kicked off three jobs at the same time in the background: the Airflow image build (Step 2b2), the Kafka deploy, and the MLflow deploy.

4. When Step 2b2 tried to run `docker build` on the EC2 instance, Docker was no longer responding. The build failed immediately with "Cannot connect to the Docker daemon."

5. On a fresh spot instance with limited memory (2 GB on a t4g.small), the Docker service, K3s (which runs its own container runtime), and MariaDB are all running at the same time. If the system runs low on memory, the Linux kernel's out-of-memory killer can shut down Docker to free up resources. This is the most likely reason the Docker service went down between the check and the build.

6. When the build failure was detected, the deploy's cleanup code sent a termination signal to the Kafka and Flask background jobs. However, the cleanup only killed the top-level shell process for each job — it did not kill the SSH connections those jobs had opened to EC2. The SSH connections stayed open, and remote output (Kafka topic creation messages) continued streaming to the terminal after the deploy had already exited and the command prompt had returned. This made the terminal appear frozen or stuck.

---

## What was changed

**`scripts/deploy/airflow_image.sh`** — Added a Docker daemon health check right before the `docker build` command (inside the build step itself, not just in the pre-flight phase). This is a second line of defense: if Docker goes down between the pre-flight check and the build, this check catches it and tries to restart the service automatically.

The check uses `timeout 10` so that a wedged Docker service (one that accepts connections but never responds) does not hang the build step forever. If the restart succeeds, the build continues normally. If it fails, the deploy stops with a clear message and a command to check Docker's logs.

```bash
# Before: went straight into docker build — if Docker was down, the build failed with
# a confusing error and no recovery attempt
ssh "$EC2_HOST" "DOCKER_BUILDKIT=1 docker build ..."

# After: checks Docker is reachable first; restarts it if not
if ! ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1"; then
    echo "Docker daemon not reachable at build time — restarting..."
    ssh "$EC2_HOST" "sudo systemctl restart docker"
    sleep 5
    # verify it came back
    ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1" || { echo "✗ Docker won't start"; return 1; }
fi
ssh "$EC2_HOST" "DOCKER_BUILDKIT=1 docker build ..."
```

**`scripts/deploy/setup.sh`** — Added `timeout 10` to the existing `docker info` commands in Step 1c3. Previously, if the Docker service was wedged (alive but not responding), the pre-flight check would hang forever. Now it times out after 10 seconds and proceeds with the auto-start recovery.

**`scripts/deploy/common.sh`** — Fixed the background job cleanup so the terminal does not freeze after a failure. The cleanup now kills child processes (the SSH connections) before killing the parent process. Previously it only killed the parent, which left the SSH connections alive — they continued streaming remote output to the terminal after the script exited. The fix also waits for each killed process to fully exit before moving on, so all output finishes before the deploy summary is printed.

```bash
# Before: killed only the parent shell — SSH children survived and kept printing
kill -TERM "$_pid" 2>/dev/null || true

# After: kills SSH children first, then the parent, then waits for cleanup
pkill -TERM -P "$_pid" 2>/dev/null || true   # kill child SSH sessions
kill -TERM "$_pid" 2>/dev/null || true        # kill the parent shell
wait "$_pid" 2>/dev/null || true              # wait for it to finish
```

---

## Why this didn't happen before

The Step 1c3 pre-flight check (added during the earlier "Docker Daemon Not Running" incident) was designed to catch the case where Docker was never started. It checks at the beginning of the deploy and auto-starts Docker if needed. It was not designed to handle Docker going down *after* the check passed.

On the old long-running t3.large instance, Docker was stable and rarely crashed. The spot instance setup uses a smaller instance type (t4g.small with 2 GB of memory), and the combination of Docker, K3s, and MariaDB running simultaneously creates more memory pressure. The Docker service is more likely to be shut down by the system's memory manager on these smaller instances.

The terminal freezing issue existed before this incident but was not noticeable because previous deploy failures either happened before background jobs started (so there were no orphaned SSH sessions) or the background jobs had already finished by the time the failure occurred.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_image.sh` | Added Docker daemon health check with `timeout 10` right before `docker build`; auto-restarts the service if it is down |
| `scripts/deploy/setup.sh` | Added `timeout 10` to `docker info` in Step 1c3 to prevent hanging on a wedged daemon |
| `scripts/deploy/common.sh` | Fixed background job cleanup: kills child SSH sessions before parents, waits for full exit to prevent terminal freeze |
