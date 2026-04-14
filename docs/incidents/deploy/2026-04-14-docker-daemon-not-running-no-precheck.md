# Docker Daemon Not Running — Pre-Flight Check Only Verified the Plugin, Not the Service

**Date:** 2026-04-14
**Severity:** High (Step 2b2 failed; Airflow image never built; all downstream steps terminated)
**Affected component:** `scripts/deploy/setup.sh` — Docker daemon pre-flight check

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy failed at the Docker build step with:

```
ERROR: Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?
✗ Airflow Docker build + K3S import (Step 2b2) FAILED
```

Here is what happened, step by step:

1. The deploy ran the pre-flight checks in Step 1c2, which included a check for Docker's buildx plugin. That check runs the command `docker buildx version` on the EC2 instance.

2. `docker buildx version` is a **client-side** command — it only checks whether the buildx plugin file exists on disk. It does not actually talk to the Docker service. This means the check can pass even when the Docker service itself is completely stopped.

3. Since the buildx check passed, the deploy moved on to the Airflow image build step (Step 2b2). This step runs `docker build`, which does need the Docker service to be running — it sends the build instructions to the Docker service for execution.

4. Because the Docker service was not running, the `docker build` command immediately failed with "Cannot connect to the Docker daemon." The deploy then sent a stop signal to the Kafka and Flask steps that were running at the same time in the background, which is why the output also showed "Terminated: 15" for those steps.

5. In short: the pre-flight checks only verified that Docker was *installed correctly* (the buildx plugin was present), but never verified that Docker was actually *running*. The gap between "installed" and "running" was not caught until the build step, at which point over a minute had already passed and multiple parallel steps had to be rolled back.

---

## What was changed

**`scripts/deploy/setup.sh`** — Added a new pre-flight check (Step 1c3) that runs on every deploy, right after the buildx plugin check. It runs `docker info` on the EC2 instance, which requires the Docker service to respond. Unlike `docker buildx version`, this command actually talks to the Docker service and will fail if the service is not running.

If the Docker service is not running, the check automatically tries to start it using `systemctl start docker`, waits a few seconds for it to finish starting up, and then verifies it is responding. If it still will not start, the deploy stops immediately with a clear error message telling you to check the Docker service logs on the EC2 instance.

If Docker is already running (which it will be on most deploys), the check passes instantly and moves on — it adds no delay to a normal deploy.

```bash
# Before: only checked if the buildx plugin file existed (client-side — does not need the service)
echo "=== Step 1c2: Verifying Docker BuildKit (buildx plugin) ==="
ssh "$EC2_HOST" "docker buildx version >/dev/null 2>&1"
# ← no check that the Docker service was actually running

# After: added Step 1c3 right below, which talks to the service directly
echo "=== Step 1c3: Verifying Docker daemon is running ==="
ssh "$EC2_HOST" "docker info >/dev/null 2>&1"
# ← if this fails, the deploy tries to start Docker before continuing
```

---

## Why this didn't happen before

The earlier pre-flight check (Step 1c2, added during the "Buildx Still Missing" incident from the same day) was focused specifically on whether the buildx plugin was installed. It used `docker buildx version` because that was the right command for checking the plugin. However, it was not noticed at the time that this command does not need the Docker service to be running — it only reads a file from disk.

On most deploys, the Docker service happens to be running already (either from the auto-bootstrap that just installed it, or because it was left running from the previous deploy). The gap only shows up when the Docker service is stopped — for example, if the service crashed, if the instance was rebooted and the service did not come back up, or if the service was manually stopped.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/setup.sh` | Added Step 1c3: pre-flight check that Docker daemon is running; auto-starts it if stopped |
