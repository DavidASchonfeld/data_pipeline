#!/bin/bash
# Module: common — shared variables, helpers, and deploy summary trap.
# Loaded by deploy.sh; assumes SCRIPT_DIR, PROJECT_ROOT, DEPLOY_LOGFILE, and DEPLOY_START have already been set.

# ── Load deploy secrets from .env.deploy ─────────────────────────────────────
# .env.deploy is gitignored and contains real AWS values (ECR registry, region).
# See .env.deploy.example for the template. This keeps AWS account IDs out of git.
ENV_DEPLOY="$PROJECT_ROOT/.env.deploy"

if [ ! -f "$ENV_DEPLOY" ]; then
    echo "ERROR: $ENV_DEPLOY not found."
    echo "Copy .env.deploy.example to .env.deploy and fill in your AWS values."
    echo "  cp .env.deploy.example .env.deploy"
    exit 1
fi

# shellcheck source=../../.env.deploy
set -a; source "$ENV_DEPLOY"; set +a  # auto-export all .env.deploy vars so child processes (e.g. python3) can read them

# Make sure the required variables were actually set in .env.deploy (in case the file is empty)
for var in ECR_REGISTRY AWS_REGION FLASK_SECRET_KEY VALIDATION_USER VALIDATION_PASS; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set in .env.deploy"
        exit 1
    fi
done
# ─────────────────────────────────────────────────────────────────────────────

# ── Shared variables ──────────────────────────────────────────────────────────
# Note: SSH config for ec2-stock (including .pem key path) lives in ~/.ssh/config
EC2_HOST="ec2-stock"
RSYNC_FLAGS="-avz --progress"  # standard flags used by all rsync calls in sub-scripts
# Home directory for the EC2 SSH user (ubuntu on Ubuntu, ec2-user on Amazon Linux)
EC2_HOME="/home/ubuntu"
EC2_DAG_PATH="$EC2_HOME/airflow/dags"
EC2_HELM_PATH="$EC2_HOME/airflow/helm"
EC2_BUILD_PATH="$EC2_HOME/dashboard_build"
EC2_DASHBOARD_PATH="$EC2_HOME/dashboard"
FLASK_IMAGE="my-flask-app:latest"
FLASK_POD="my-kuber-pod-flask"
ECR_IMAGE="$ECR_REGISTRY/my-flask-app:latest"
# ─────────────────────────────────────────────────────────────────────────────

# ── Warning/Error Summary ─────────────────────────────────────────────────────
# Runs whenever the script exits — whether it succeeded, was stopped by an error, or called exit 1 directly.
_print_deploy_summary() {
    local exit_code="${_DEPLOY_EXIT:-$?}"  # prefer exit code captured by the trap before _mark_deploy_inactive ran
    set +e              # turn off 'stop on error' so the summary always finishes printing
    # Kill any background jobs still running so the terminal isn't frozen for minutes after a failure.
    # pkill -P kills child processes (SSH sessions) BEFORE killing the parent — if we killed
    # the parent first, the SSH children would become orphans (reparented to init) and continue
    # streaming remote output to the terminal, making it appear frozen even after the script exits.
    if [ "$exit_code" -ne 0 ]; then
        for _pid_var in AIRFLOW_BUILD_PID KAFKA_PID MLFLOW_PID FLASK_PID; do
            local _pid="${!_pid_var:-}"
            if [ -n "$_pid" ]; then
                pkill -TERM -P "$_pid" 2>/dev/null || true  # kill child SSH sessions first
                kill -TERM "$_pid" 2>/dev/null || true       # then the parent shell
                wait "$_pid" 2>/dev/null || true             # wait for it to finish exiting
            fi
        done
    fi
    sleep 0.2           # give the tee process a moment to finish writing everything to the log file before we search it
    # Search the log for warnings and errors, removing duplicate lines while keeping them in order
    local summary_lines
    summary_lines=$(grep -E "(WARNING|ERROR|⚠|DeprecationWarning|DEPRECATION:|FutureWarning|UserWarning|✗)" \
        "$DEPLOY_LOGFILE" \
        | grep -v -- "--ignore-not-found" \
        | awk '!seen[$0]++') || true  # || true: if grep finds nothing it exits non-zero — this prevents that from stopping the script
    echo ""
    echo "=================================================================="
    if [ "$exit_code" -eq 0 ]; then
        echo "  DEPLOY COMPLETE"
    else
        echo "  DEPLOY FAILED  (exit code: $exit_code)"
        # Show a clear note when the user pressed Ctrl+C — makes it obvious later why the deploy stopped
        if [ "${DEPLOY_INTERRUPTED:-false}" = true ]; then
            echo "  Stopped by: Ctrl+C (manually interrupted)"
        fi
        # Show the exact bash command that triggered the failure — set by the ERR trap in deploy.sh
        if [ -n "${DEPLOY_FAILED_CMD:-}" ]; then
            echo "  Failed command: $DEPLOY_FAILED_CMD"
        fi
    fi
    # Calculate how many seconds the deploy took using bash's built-in $SECONDS timer
    local elapsed=$(( SECONDS - DEPLOY_START ))
    local elapsed_min=$(( elapsed / 60 ))
    local elapsed_sec=$(( elapsed % 60 ))
    printf "  Elapsed time: %dm %02ds\n" "$elapsed_min" "$elapsed_sec"
    echo "  -- Warnings & Errors -------------------------------------------"
    if [ -z "$summary_lines" ]; then
        if [ "$exit_code" -ne 0 ]; then
            # No WARNING/ERROR keywords were found — show the last 15 log lines so there is
            # always a visible trail; this catches failures like SSH exit 255 that print nothing
            echo "  No WARNING/ERROR keywords found. Last 15 log lines:"
            echo ""
            tail -n 15 "$DEPLOY_LOGFILE" | while IFS= read -r line; do
                echo "    > $line"
            done
            echo ""
            echo "  Script exited with errors — check items above and logs for details."
        else
            echo "  (none)"
        fi
    else
        echo ""
        while IFS= read -r line; do
            echo "    > $line"
        done <<< "$summary_lines"
        echo ""
        if [ "$exit_code" -eq 0 ]; then
            echo "  Script ran to completion despite the above — review before closing."
        else
            echo "  Script exited with errors — check items above and logs for details."
        fi
    fi
    echo "=================================================================="
    echo "  Full log: $DEPLOY_LOGFILE"
    echo "=================================================================="
    echo ""
}
# ─────────────────────────────────────────────────────────────────────────────

# ── K8s and K3S helpers ───────────────────────────────────────────────────────
# Pipe a Docker image into K3S containerd on EC2; grep_term is the string used to verify it was imported.
# Avoids writing a temporary tar file — pipes directly to k3s ctr images import (saves disk space on EC2).
# Retries once with a short delay — concurrent K3s containerd writes from parallel background jobs can
# produce a transient "failed commit on ref" race condition on the first attempt.
import_image_to_k3s() {
    local image_name="$1" grep_term="$2"
    for _attempt in 1 2; do
        if ssh "$EC2_HOST" "
            echo 'Importing $image_name into K3S containerd (attempt $_attempt/2)...' &&
            docker save '$image_name' | sudo k3s ctr images import - &&
            echo 'Verifying image is visible to K3S...' &&
            sudo k3s ctr images ls | grep '$grep_term'
        "; then
            return 0
        fi
        [ "$_attempt" -lt 2 ] && echo "K3S import attempt $_attempt failed, retrying in 5s..." && sleep 5
    done
    return 1
}

# Apply a K8s generic secret idempotently — creates if absent, updates if present.
# Usage: apply_k8s_secret <namespace> <secret_name> [--from-literal=KEY=VAL ...]
# Extra args are passed directly to kubectl create secret generic (safe when values have no spaces).
apply_k8s_secret() {
    local namespace="$1" secret_name="$2"
    shift 2  # remaining args are passed through to kubectl create secret
    ssh "$EC2_HOST" "kubectl create secret generic '$secret_name' -n '$namespace' $* \
        --dry-run=client -o yaml | kubectl apply -f -"
}

# Delete a pod by label selector on EC2, wait for it to disappear, then apply a manifest.
# Useful for plain Pods (not Deployments) that must be deleted before a new spec can be applied.
restart_pod() {
    local namespace="$1" manifest="$2" pod_selector="$3"
    ssh "$EC2_HOST" "
        kubectl delete pod -l '$pod_selector' -n '$namespace' --ignore-not-found=true &&
        kubectl wait --for=delete pod -l '$pod_selector' -n '$namespace' --timeout=60s 2>/dev/null || true &&
        kubectl apply -f '$manifest' -n '$namespace'
    "
}
# ─────────────────────────────────────────────────────────────────────────────

# ── SSH readiness helper ──────────────────────────────────────────────────────
# Waits for sshd to accept connections — a fresh spot instance can take 2-5 min to boot
# and start sshd, so we retry for up to 6 minutes before giving up.
# StrictHostKeyChecking=accept-new: automatically trusts a brand-new host key (safe after
# deploy.sh clears known_hosts for the replaced instance) but still rejects unexpected
# key changes on already-known hosts.
_wait_ssh_ready() {
    # 36 attempts × 10 s = 6 min — doubled from 3 min to handle slow spot instance boots
    for _attempt in $(seq 1 36); do
        if ssh -o StrictHostKeyChecking=accept-new "$EC2_HOST" true 2>/dev/null; then
            return 0
        fi
        echo "SSH not ready (attempt $_attempt/36), retrying in 10s..."
        [ "$_attempt" -lt 36 ] && sleep 10
    done
    echo "✗ EC2 SSH unreachable after 36 attempts (6 min)"
    return 1
}
# ─────────────────────────────────────────────────────────────────────────────

# ── K3s node readiness helper ─────────────────────────────────────────────────
# SSH can succeed 2-3 min before K3s finishes starting — without this wait,
# Helm and Kafka apply against a NotReady node and pods get stuck in Pending.
_wait_k3s_ready() {
    echo "=== Waiting for K3s node to be Ready (up to 5 minutes) ==="
    # 30 attempts × 10s = 5 min — matches the wait in bootstrap.sh and user-data.sh.tpl
    for _attempt in $(seq 1 30); do
        if ssh "$EC2_HOST" "kubectl get nodes 2>/dev/null | grep -q ' Ready'"; then
            echo "✓ K3s node is Ready (attempt $_attempt)"
            ssh "$EC2_HOST" "kubectl get nodes"  # print node status so the log shows it
            return 0
        fi
        echo "K3s not ready yet (attempt $_attempt/30), retrying in 10s..."
        [ "$_attempt" -lt 30 ] && sleep 10
    done
    echo "✗ K3s node did not become Ready after 5 minutes"
    return 1
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Background job error helper ───────────────────────────────────────────────
# _wait_bg PID label — waits for a background job to finish, then prints success or failure and exits if it failed.
# WHY this is needed: bash's 'stop on error' setting does not apply to background jobs (&).
# Without this function, a failed background SSH job would disappear silently and the script
# would keep running as if nothing went wrong. This function catches that.
_wait_bg() {
    local pid=$1 label=$2
    if wait "$pid"; then
        echo "✓ $label done"
    else
        echo "✗ $label FAILED"
        exit 1
    fi
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Sleep/Wake helpers ────────────────────────────────────────────────────────
# These functions let the deploy script wake up a sleeping instance, put it to sleep,
# and prevent the auto-sleep Lambda from shutting down during an active deploy.
# Use the same AWS SSO profile as terraform.sh for all AWS CLI calls
export AWS_PROFILE="${AWS_PROFILE:-terraform-dev}"

# Wake the instance by telling the ASG to launch a new server from the baked AMI
step_wake() {
    echo "=== Waking pipeline instance ==="
    # Record activity so the auto-sleep timer resets
    aws ssm put-parameter --name "/pipeline/last-activity-timestamp" \
        --value "$(date +%s)" --type String --overwrite \
        --region "$AWS_REGION" 2>/dev/null || true

    # Check if the instance is already running
    local desired
    desired=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names pipeline-asg \
        --region "$AWS_REGION" \
        --query 'AutoScalingGroups[0].DesiredCapacity' --output text 2>/dev/null || echo "1")

    if [ "$desired" = "0" ]; then
        # Tell the ASG to launch an instance from the pre-baked AMI
        echo "ASG desired=0 — scaling to 1..."
        aws autoscaling set-desired-capacity \
            --auto-scaling-group-name pipeline-asg \
            --desired-capacity 1 \
            --region "$AWS_REGION"
        echo "ASG scaling initiated. Waiting for instance + SSH..."
    else
        echo "Instance is already running (desired=$desired)"
    fi

    # Wait until SSH is available so the deploy can proceed
    _wait_ssh_ready
    _wait_k3s_ready  # K3s takes 3-5 min after boot — wait before any kubectl/helm operations
    echo "=== Instance is awake and SSH-ready ==="
}

# Scales ASG to 1 if sleeping, but does NOT wait for SSH.
# Used by --provision so Terraform can update the SSH security group rule before we connect.
_scale_asg_up_if_sleeping() {
    local desired
    desired=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names pipeline-asg \
        --region "$AWS_REGION" \
        --query 'AutoScalingGroups[0].DesiredCapacity' --output text 2>/dev/null || echo "1")
    if [ "$desired" = "0" ]; then
        echo "ASG desired=0 — scaling to 1 (Terraform will update security group before SSH)..."
        aws autoscaling set-desired-capacity \
            --auto-scaling-group-name pipeline-asg \
            --desired-capacity 1 \
            --region "$AWS_REGION"
        # Reset activity timer so auto-sleep doesn't kill the instance right after launch
        aws ssm put-parameter --name "/pipeline/last-activity-timestamp" \
            --value "$(date +%s)" --type String --overwrite \
            --region "$AWS_REGION" 2>/dev/null || true
        echo "Instance launch initiated — booting while Terraform runs."
    else
        echo "Instance already running (desired=$desired)"
    fi
}

# Put the instance to sleep by scaling the ASG to 0 (the instance terminates to save money)
step_sleep() {
    echo "=== Putting pipeline instance to sleep ==="
    # Scale the ASG to 0 so the instance terminates and stops costing money
    aws autoscaling set-desired-capacity \
        --auto-scaling-group-name pipeline-asg \
        --desired-capacity 0 \
        --region "$AWS_REGION"
    echo "ASG desired capacity set to 0. Instance will terminate shortly."
}

# Tell the auto-sleep Lambda that a deploy is running so it does not shut us down mid-deploy
_mark_deploy_active() {
    aws ssm put-parameter --name "/pipeline/deploy-active" \
        --value "true" --type String --overwrite \
        --region "$AWS_REGION" 2>/dev/null || true
    # Also refresh the activity timer so the sleep countdown resets
    aws ssm put-parameter --name "/pipeline/last-activity-timestamp" \
        --value "$(date +%s)" --type String --overwrite \
        --region "$AWS_REGION" 2>/dev/null || true
}

# Tell the auto-sleep Lambda that the deploy is done and normal sleep rules can resume
_mark_deploy_inactive() {
    aws ssm put-parameter --name "/pipeline/deploy-active" \
        --value "false" --type String --overwrite \
        --region "$AWS_REGION" 2>/dev/null || true
    # Refresh the activity timer so the instance stays up for the full idle timeout after deploy
    aws ssm put-parameter --name "/pipeline/last-activity-timestamp" \
        --value "$(date +%s)" --type String --overwrite \
        --region "$AWS_REGION" 2>/dev/null || true
}
# ─────────────────────────────────────────────────────────────────────────────
