# Docker Build Failed — Server Overloaded by Three Parallel Jobs

**Date:** 2026-04-17
**Severity:** High (Step 2b2 failed; Airflow image never built; deploy terminated)
**Affected components:** `scripts/deploy/airflow_image.sh`, `scripts/deploy/kafka.sh`, `scripts/deploy/common.sh`

---

## What was the problem

The deploy failed at the Airflow image build step with these errors:

```
Error response from daemon: a prune operation is already running
error: Internal error occurred: unable to upgrade connection: container not found ("kafka")
send stream ended without EOF / connection reset by peer (K3S containerd socket)
ERROR: failed to build: failed to receive status: rpc error: code = Unavailable desc = error reading from server: EOF
✗ Airflow Docker build + K3S import (Step 2b2) FAILED
```

Here is what happened, step by step:

1. The deploy reached Phase 2 and launched three jobs **at the same time**: the Airflow Docker image build, the Kafka image deploy, and the MLflow image deploy.

2. All three jobs ran heavy operations on the EC2 server simultaneously. The Kafka job was pulling a large image into the container runtime. The MLflow job was importing a large image into the container runtime. The Airflow job was running a Docker image build AND also importing its image into the container runtime.

3. The EC2 server (a small spot instance with 2 GB of memory) became overwhelmed. The container runtime — the software that manages all running images and containers — started dropping connections under the combined load.

4. Docker's build process lost its connection to its own internal storage backend mid-build and failed with `error reading from server: EOF`. This is like trying to write to a hard drive while the drive cable gets unplugged — Docker couldn't finish saving the image it was building.

5. Separately, when the Kafka job tried to run a command inside the Kafka container to create topics, the container runtime reported `container not found` even though the Kafka pod was showing as "Ready." This is a timing bug: Kubernetes marks a pod Ready slightly before the container runtime has fully registered it for remote command execution.

6. A stale Docker cleanup job from a previous deploy was still running when the new deploy started its own cleanup. Docker rejected the second cleanup with `a prune operation is already running`.

---

## What was changed

### `scripts/deploy/airflow_image.sh` — Retry Docker build on connection failure

Before this fix, if the Docker build failed for any reason, the deploy stopped immediately. Now the build retries up to 3 times. Between each failed attempt, Docker is fully restarted so its internal connections are reset.

```bash
# Before: one attempt, instant failure
ssh "$EC2_HOST" "DOCKER_BUILDKIT=1 docker build -t airflow-dbt:$BUILD_TAG ..." || return 1

# After: up to 3 attempts; Docker restarts between failures to reset lost connections
for _build_attempt in 1 2 3; do
    if ssh "$EC2_HOST" "
        echo 'Building ... (attempt $_build_attempt/3)...' &&
        DOCKER_BUILDKIT=1 docker build -t airflow-dbt:$BUILD_TAG ...
    "; then
        break  # build succeeded — exit the retry loop
    fi
    if [ "$_build_attempt" -lt 3 ]; then
        echo "Docker build attempt $_build_attempt failed — restarting Docker daemon and retrying in 20s..."
        ssh "$EC2_HOST" "sudo systemctl restart docker" || true
        sleep 20
    else
        echo "✗ Docker build failed after 3 attempts"
        return 1
    fi
done
```

### `scripts/deploy/airflow_image.sh` — Wait for any in-progress Docker cleanup before starting a new one

Before this fix, if a Docker image cleanup job from a previous deploy was still running, the new cleanup would print an error and silently do nothing. Now it waits for the running cleanup to finish, then runs its own.

```bash
# Before: one attempt, printed error "prune already running" if another was in progress
docker image prune -f || true

# After: retries up to 5 times, waiting 10s between each attempt until the other finishes
for _p in 1 2 3 4 5; do
    out=$(docker image prune -f 2>&1) && echo "$out" && break
    echo "$out" | grep -q 'prune operation is already running' \
        && echo "Prune already running (attempt $_p/5) — waiting 10s..." && sleep 10 \
        || { echo "$out"; break; }
done || true
```

### `scripts/deploy/kafka.sh` — Retry topic creation when container is temporarily unreachable

Before this fix, if `kubectl exec` (the command used to run a program inside the Kafka container) reported "container not found," the topic creation failed and the deploy stopped. Now it retries up to 3 times, waiting 10 seconds between each attempt.

```bash
# Before: one attempt, failed immediately if container wasn't ready for exec
kubectl exec kafka-0 -n kafka -- ... --topic stocks-financials-raw ...

# After: retries 3 times; the container runtime sometimes takes a few extra seconds
# to register a container after Kubernetes says the pod is ready
for _t in 1 2 3; do
    kubectl exec kafka-0 -n kafka -- ... --topic stocks-financials-raw ... \
    && echo 'Topic stocks-financials-raw ready.' && break
    [ "$_t" -lt 3 ] && echo "Topic create attempt $_t failed, retrying in 10s..." && sleep 10
done
```

Same change applied to the `weather-hourly-raw` topic.

### `scripts/deploy/common.sh` — Check container runtime health before retrying image import

The function that imports the Airflow image into K3S's container runtime already had retry logic. Added a check before each retry to verify the container runtime's communication socket is actually responding. If the socket is down, it waits 30 seconds (instead of the normal 15) to give the runtime more time to recover.

```bash
# Before: always waited the same 15s before retrying, even if the socket was completely down
[ "$_attempt" -lt 5 ] && echo "K3S import attempt $_attempt failed, retrying in 15s..." && sleep 15

# After: checks if the container runtime socket is live; waits longer if it needs to recover
if ssh "$EC2_HOST" "sudo k3s ctr version >/dev/null 2>&1"; then
    sleep 15  # socket is fine — brief pause is enough
else
    echo "K3S containerd socket unresponsive — waiting 30s for it to recover..."
    sleep 30  # socket is down — give it more time before retrying
fi
```

---

## Why this didn't happen before

The three parallel background jobs have always run at the same time. On this particular deploy, the server happened to be under more memory pressure than usual (possibly from a previous deploy that hadn't fully cleaned up, or from the spot instance being freshly started with cold caches). The extra memory pressure pushed the container runtime over its limit.

The retry logic added by this fix means that on the next deploy — even if the same overload happens — the build will automatically wait, restart, and try again without any manual intervention.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_image.sh` | Retry Docker build up to 3 times with daemon restart between failures; wait for any in-progress Docker cleanup before starting a new one |
| `scripts/deploy/kafka.sh` | Retry `kubectl exec` topic creation up to 3 times with 10s delay between attempts |
| `scripts/deploy/common.sh` | Check K3S containerd socket health before each retry; wait 30s instead of 15s if socket is unresponsive |
