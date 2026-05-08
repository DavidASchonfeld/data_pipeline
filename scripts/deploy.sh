#!/bin/bash
# Deploy updated DAGs and dashboard to EC2 production.
# Usage:
#   ./scripts/deploy.sh                  — full deploy (Docker build, Kafka, MLflow, Flask, Helm, pods)
#   ./scripts/deploy.sh --dags-only      — fast path: only sync DAG files + restart Airflow pods (~5-7 min)
#                                          Use when you only changed .py files in airflow/dags/
#                                          For Dockerfile, values.yaml, Kafka, MLflow, or dashboard changes — run the full deploy.
#   ./scripts/deploy.sh --provision      — run terraform apply first (updates security group IP), then full deploy
#                                          Use when creating a new instance or switching networks
#   ./scripts/deploy.sh --snowflake-setup — bootstrap all Snowflake objects (warehouse, DB, schemas, role, user)
#                                          Run once on a fresh Snowflake account or after a full project teardown.
#                                          Requires SNOWFLAKE_ADMIN_USER, SNOWFLAKE_ADMIN_PASSWORD, SNOWFLAKE_PASSWORD in .env.deploy.
#                                          Safe to re-run — all statements are CREATE IF NOT EXISTS.
#   ./scripts/deploy.sh --bake-ami       — create a golden AMI snapshot from the running instance

# Exit immediately if any command fails, unset variable is used, or pipe fails
set -euo pipefail

# Capture the last failing bash command so _print_deploy_summary can show it.
# ERR fires on every non-zero exit before the EXIT trap runs, so DEPLOY_FAILED_CMD
# always holds the command that triggered the failure.
DEPLOY_FAILED_CMD=""
trap 'DEPLOY_FAILED_CMD="$BASH_COMMAND"' ERR

# Set a flag when the user presses Ctrl+C so the summary can say why it stopped
DEPLOY_INTERRUPTED=false
trap 'DEPLOY_INTERRUPTED=true; exit 130' INT

# ── Log setup ─────────────────────────────────────────────────────────────────
# Save all output to a log file so we can search it for the end-of-run summary
DEPLOY_LOGFILE="/tmp/deploy-last.log"
exec > >(tee "$DEPLOY_LOGFILE") 2>&1  # tee prints to the screen AND saves to the log file; 2>&1 also captures error output
DEPLOY_START=$SECONDS  # save the start time so we can show how long the deploy took
# ─────────────────────────────────────────────────────────────────────────────

# ── Module loading ────────────────────────────────────────────────────────────
# Resolve paths relative to this script so deploy.sh can be called from any directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_DIR="$SCRIPT_DIR/deploy"

# Load helper files in the right order — common.sh must go first since everything else depends on it
source "$DEPLOY_DIR/common.sh"       # shared vars, _wait_bg, _print_deploy_summary, .env.deploy
source "$DEPLOY_DIR/bootstrap.sh"    # step_auto_bootstrap (installs K3s, Docker, Helm, MariaDB on fresh spot instances)
source "$DEPLOY_DIR/setup.sh"        # step_setup
source "$DEPLOY_DIR/sync.sh"         # step_sync_dags, step_sync_helm_dockerfile, step_sync_manifests_secrets
source "$DEPLOY_DIR/snowflake.sh"    # step_snowflake_setup
source "$DEPLOY_DIR/airflow_image.sh" # step_build_airflow_image
source "$DEPLOY_DIR/kafka.sh"        # step_deploy_kafka
source "$DEPLOY_DIR/mlflow.sh"       # step_deploy_mlflow, step_fix_mlflow_experiment, step_mlflow_portforward
source "$DEPLOY_DIR/flask.sh"        # step_deploy_flask, step_verify_flask
source "$DEPLOY_DIR/airflow_pods.sh" # step_helm_upgrade, step_verify_airflow_image, step_restart_airflow_pods, step_setup_ml_venv
source "$DEPLOY_DIR/ami.sh" 2>/dev/null || true   # ami.sh may not exist yet on first deploy

trap '_DEPLOY_EXIT=$?; _print_deploy_summary' EXIT  # capture exit code first so summary always sees the real exit code
# ─────────────────────────────────────────────────────────────────────────────

# ── Argument parsing ──────────────────────────────────────────────────────────
# --dags-only: fast path for DAG-only changes — skips Docker build, Kafka, MLflow, Flask, Helm
DAGS_ONLY=false
# --provision: run terraform apply before the deploy to ensure EC2 infrastructure is current
PROVISION=false
# --snowflake-setup: bootstrap Snowflake objects before the rest of the deploy (one-time or after teardown)
SNOWFLAKE_SETUP=false
# --fix-ml-venv: repair a broken ml-venv in the running scheduler pod without a full redeploy (~60s)
FIX_ML_VENV=false
# --bake-ami: create a golden AMI from the running instance for fast future boots
BAKE_AMI=false
for _arg in "$@"; do
    case "$_arg" in
        --dags-only)       DAGS_ONLY=true ;;
        --provision)       PROVISION=true ;;
        --snowflake-setup) SNOWFLAKE_SETUP=true ;;
        --fix-ml-venv)     FIX_ML_VENV=true ;;
        --bake-ami)        BAKE_AMI=true ;;
        *) echo "ERROR: Unknown argument: $_arg"; exit 1 ;;
    esac
done
[ "$DAGS_ONLY" = true ]       && echo "--- Mode: --dags-only (skipping Docker build, Kafka, MLflow, Flask, Helm) ---"
[ "$PROVISION" = true ]       && echo "--- Mode: --provision (running Terraform before deploy) ---"
[ "$SNOWFLAKE_SETUP" = true ] && echo "--- Mode: --snowflake-setup (bootstrapping Snowflake objects before deploy) ---"
[ "$FIX_ML_VENV" = true ]     && echo "--- Mode: --fix-ml-venv (repairing ml-venv in running scheduler pod only) ---"
[ "$BAKE_AMI" = true ]        && echo "--- Mode: --bake-ami (creating golden AMI snapshot) ---"

# --fix-ml-venv: skip the full deploy and only rebuild the ml-venv in the running pod
# Useful after a deploy where step_setup_ml_venv printed a WARNING — no pod restart or Docker build needed
if [ "$FIX_ML_VENV" = true ]; then
    _wait_scheduler_exec  # confirm the scheduler container is exec-ready before attempting pip install
    step_setup_ml_venv
    exit 0
fi

# --bake-ami: snapshot the running instance so future boots are fast (~3-5 min instead of ~60 min)
if [ "$BAKE_AMI" = true ]; then
    "$DEPLOY_DIR/ami.sh" bake
    exit 0
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Rollback procedure ────────────────────────────────────────────────────────
# If the Flask pod fails to start after a deploy, recover using the previous image:
#
#   1. SSH into EC2:
#        ssh ec2-stock
#   2. Re-tag the previous image as latest and re-apply the manifest:
#        docker tag my-flask-app:previous my-flask-app:latest
#        docker tag my-flask-app:previous $ECR_REGISTRY/my-flask-app:latest
#        docker push $ECR_REGISTRY/my-flask-app:latest
#   3. Delete and recreate the Flask pod so K3S pulls the restored image:
#        kubectl delete pod my-kuber-pod-flask -n default --ignore-not-found=true
#        kubectl apply -f ~/dashboard/manifests/pod-flask.yaml
#        kubectl wait pod/my-kuber-pod-flask -n default --for=condition=Ready --timeout=90s
#
# The `my-flask-app:previous` image is tagged at the start of Step 4 on every deploy,
# so it always points to whatever was running before the current deploy started.
# ─────────────────────────────────────────────────────────────────────────────

# ── SSH readiness ─────────────────────────────────────────────────────────────
# Instance is always-on (spot) — just wait for SSH to be ready before deploying.
# For --provision: Terraform runs first (updating security group IP), then SSH is checked.
if [ "$PROVISION" = true ]; then
    echo "=== Phase 0: Provisioning infrastructure via Terraform ==="
    "$SCRIPT_DIR/deploy/terraform.sh" apply  # updates SSH ingress rule to current IP
    echo "=== Waiting for SSH (security group updated) ==="
    _wait_ssh_ready 90  # fresh instance needs up to 15 min for user-data bootstrap (Docker, K3s, apt)
    _wait_k3s_ready
    echo "=== SSH ready ==="
else
    echo "=== Verifying SSH connectivity ==="
    # Clear stale known_hosts entry — spot replacement gives the instance a new host key,
    # and StrictHostKeyChecking=accept-new silently fails when the old key is still cached.
    _EC2_IP=$(ssh -G "$EC2_HOST" 2>/dev/null | awk '/^hostname/ {print $2; exit}')
    ssh-keygen -R "$EC2_HOST" &>/dev/null || true  # remove alias entry
    [ -n "$_EC2_IP" ] && ssh-keygen -R "$_EC2_IP" &>/dev/null || true  # remove IP entry
    _check_ssh_prereqs  # fast-fail if security group IP has drifted since last --provision
    _wait_ssh_ready
    _wait_k3s_ready
    echo "=== SSH ready ==="
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Early AMI bake cancellation ───────────────────────────────────────────────
# Cancel any bake from the previous deploy before launching parallel jobs.
# The bake runs docker system prune and stops k3s mid-flight, which disrupts
# concurrent docker build, containerd, and kubectl operations in this deploy.
if declare -f cancel_in_progress_bake > /dev/null 2>&1 && [ -f "${_AMI_LOCKFILE:-/tmp/ami-bake.lock}" ]; then
    _bake_lock_age=$(( $(date +%s) - $(stat -f %m "${_AMI_LOCKFILE:-/tmp/ami-bake.lock}" 2>/dev/null || echo 0) ))
    if [ "$_bake_lock_age" -lt 3600 ]; then
        echo "=== AMI bake in progress — cancelling before deploy to prevent Docker/K3s conflicts ==="
        cancel_in_progress_bake
    else
        rm -f "${_AMI_LOCKFILE:-/tmp/ami-bake.lock}"  # stale lock from a crashed bake
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════
# Phase -1: Snowflake bootstrap (only with --snowflake-setup flag)
# Runs first so all Snowflake objects exist before the pipeline DAGs are deployed.
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$SNOWFLAKE_SETUP" = true ]; then
    echo "=== Phase -1: Bootstrapping Snowflake infrastructure ==="
    step_snowflake_setup  # creates warehouse, DB, schemas, role, user via scripts/snowflake_setup.sql
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Setup + Sync (always runs)
# ═══════════════════════════════════════════════════════════════════════════════

step_setup  # Steps 1, 1c, 1b: EC2 dirs, kubectl chmod, Python syntax validation

step_sync_dags  # Step 2: rsync airflow/dags/ to EC2

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Parallel heavy builds (full deploy only)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Sync the Dockerfile and Helm values first — the Airflow image build (below) needs these files before it can start
    step_sync_helm_dockerfile  # Steps 2b, 2b1: rsync Helm values + Dockerfile to EC2

    # Add a timestamp to the image tag so each deploy gets a unique name, forcing K3S to treat it as a new image
    BUILD_TAG="3.1.8-dbt-$(date +%Y%m%d%H%M%S)"
    echo "Build tag: $BUILD_TAG"

    # Remove stale NotReady nodes BEFORE launching background jobs — Kafka and MLflow pods would otherwise
    # get NoSchedule/NoExecute taints and sit Pending the full timeout (AMI replacement leaves old node in K3s etcd)
    _cleanup_stale_nodes

    # Run the heaviest step (apt + 2 pip venvs) FIRST in the foreground — running it in
    # parallel with Kafka+MLflow on a t4g.large (8GB/2vCPU) starves the host and drops SSH.
    # Kafka and MLflow are k8s manifest applies (idempotent, light), so 2-way parallel after is safe.
    step_build_airflow_image  # Step 2b2: Docker build + K3S import (~10-30s with cached layers, 2-5 min from scratch)

    # Run the two remaining independent steps in parallel — both are mostly idempotent kubectl applies
    # _wait_bg checks whether each one succeeded — bash's built-in error checking doesn't catch background job failures
    step_deploy_kafka &  # Steps 2b3-2b4: Kafka manifest rsync + image pull + StatefulSet deploy (~7-10 min)
    KAFKA_PID=$!

    step_deploy_mlflow &  # Steps 2b5-2b6: MLflow manifest rsync + image import + Deployment deploy (~3-5 min)
    MLFLOW_PID=$!
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3: Secrets (runs while background jobs execute — fast, ~15s)
# ═══════════════════════════════════════════════════════════════════════════════

# Must finish before Flask and Helm — pods need these secrets to read their environment variables when they start up
step_sync_manifests_secrets  # Steps 2c-2c3: rsync all manifests + apply K8s secrets

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4: Flask (parallel) + Helm upgrade (full deploy only)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Flask has no dependency on Kafka or MLflow — it queries Snowflake directly.
    # Start it now in the background so the dashboard comes up ~10 min earlier
    # while Kafka and MLflow continue building in the background.
    step_deploy_flask &  # Steps 3-6: dashboard rsync, ECR setup, Flask build/push, Flask pod restart
    FLASK_PID=$!

    # Airflow image build already finished in Phase 2 (now serialized) — no wait needed before Helm upgrade
    step_helm_upgrade  # Steps 2d + 2e: helm upgrade + apply Airflow service manifest
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5: Airflow pod restarts (always runs — reloads DAG files in all modes)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Kafka and MLflow must be ready before Airflow pods start — DAGs connect to both on startup.
    # Flask doesn't need them (it uses Snowflake), so we only wait here, not before Flask.
    _wait_bg $KAFKA_PID  "Kafka deploy (Steps 2b3-2b4)"
    _wait_bg $MLFLOW_PID "MLflow deploy (Steps 2b5-2b6)"

    # Check that K3S didn't automatically delete the Airflow image to free disk space during the ~20 min gap since we built it
    step_verify_airflow_image  # Step 7a
fi

# Restart the scheduler, dag-processor, and triggerer pods — waiting for all three in parallel takes ~360s max instead of 18 min one at a time
step_restart_airflow_pods  # Step 7

# Rebuild the ML Python environment in the scheduler pod — it gets wiped every time the pod restarts
step_setup_ml_venv  # Step 7b

step_fix_mlflow_experiment  # Step 7c: reset MLflow artifact root via sqlite3 (safe to run multiple times)

step_cleanup_dead_pods  # Step 7e: delete Evicted/Error/Unknown pods left over from prior restarts

step_mlflow_portforward  # Step 7d: restart port-forward for MLflow UI on EC2 localhost:5500

if [ "$DAGS_ONLY" = false ]; then
    # Flask was launched in the background in Phase 4 — it's likely already done by now.
    # Wait here to catch any errors before declaring the deploy complete.
    _wait_bg $FLASK_PID "Flask deploy (Steps 3-6)"
    step_verify_flask  # Step 8: confirm Flask pod is Ready
fi

echo ""
echo "=== Done! ==="
echo ""
# Resolve the Elastic IP from SSH config so the printed URL is always correct
_DASHBOARD_IP=$(ssh -G ec2-stock 2>/dev/null | awk '/^hostname / {print $2}')

echo "Verify in browser:"
echo "  Dashboard:   http://${_DASHBOARD_IP:-52.70.211.1}:32147/dashboard/  (public — no tunnel needed)"
echo "  Airflow UI:  http://localhost:30080                                 (requires SSH tunnel — see below)"
echo ""

# Git holds the master copy of all manifests; EC2 has a synced copy for running kubectl commands directly on the server
echo "=== kubectl Workflow ==="
echo "Manifests are version-controlled in Git and synced to EC2:"
echo "  Local (Git):  airflow/manifests/   dashboard/manifests/"
echo "  EC2:          $EC2_HOME/airflow/manifests/   $EC2_HOME/dashboard/manifests/"
echo ""
echo "To apply/update manifests from your Mac:"
echo "  kubectl apply -f airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
echo "  kubectl apply -f dashboard/manifests/pod-flask.yaml -n default"
echo ""
echo "To apply directly from EC2:"
echo "  ssh ec2-stock"
echo "  kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
echo ""
echo "=== SSH Tunnel (Airflow + MLflow only — run on Mac before opening browser) ==="
echo "  ssh -L 30080:localhost:30080 -L 5500:localhost:5500 -L 6443:localhost:6443 ec2-stock"
echo ""

# ── Auto-bake AMI ─────────────────────────────────────────────────────────────
# After a successful full deploy, silently snapshot the server so the next cold boot
# uses the latest code and starts in 3-5 min instead of 60 min.
# This block only runs if every deploy step above passed — so we never snapshot a broken state.
if [ "$DAGS_ONLY" = false ] && declare -f step_bake_ami > /dev/null 2>&1; then
    _AMI_LOCKFILE="/tmp/ami-bake.lock"

    # If a previous bake is still running, cancel it — the new deploy has newer code,
    # so we never want an older (potentially buggy) AMI to finish baking.
    if [ -f "$_AMI_LOCKFILE" ]; then
        _lock_age=$(( $(date +%s) - $(stat -f %m "$_AMI_LOCKFILE" 2>/dev/null || echo 0) ))
        if [ "$_lock_age" -lt 3600 ]; then
            # Previous bake is still active — cancel it so the latest deploy always wins
            echo "=== Cancelling previous AMI bake (newer deploy takes priority) ==="
            cancel_in_progress_bake
        else
            rm -f "$_AMI_LOCKFILE"  # stale lock from a crashed bake — clean it up
        fi
    fi

    # Refresh AWS SSO up front so any browser login prompt appears here in the foreground
    # rather than ~30 sec after "DEPLOY COMPLETE" prints from the detached background bake.
    # The bake calls AWS from its very first step (ASG lookup) through its last (AMI cleanup),
    # so without a valid session the bake cannot even start.
    echo "=== Checking AWS SSO session for AMI bake ==="
    echo "  The bake uses AWS CLI throughout its full 15-25 min run:"
    echo "    - before it starts: look up the EC2 instance ID from the ASG"
    echo "    - during:           create the AMI, poll its state, update the launch template"
    echo "    - after:             deregister old AMIs and their snapshots to save cost"
    echo "  Logging in now (if needed) avoids a delayed browser prompt after deploy finishes."
    if declare -f _ensure_aws_auth > /dev/null 2>&1; then
        _ensure_aws_auth || echo "WARNING: AWS auth refresh failed — background bake may prompt again or fail"
    fi

    # Always start a fresh bake after any cancellation/cleanup
    echo "=== Baking AMI in background ==="
    echo "  A snapshot of the server is being saved silently (takes 15-25 min)."
    echo "  Services will briefly restart (~60 sec) for a clean snapshot — the server stays usable."
    echo "  AWS SSO was refreshed above, so no further login prompt is expected during the bake."
    echo "  Progress: tail -f /tmp/ami-bake.log"
    > /tmp/ami-bake.log   # truncate previous bake log before starting new one
    # Run the bake in a detached shell — nohup keeps it alive after this terminal closes
    nohup bash -c "
        trap 'rm -f $_AMI_LOCKFILE' EXIT
        SCRIPT_DIR='$SCRIPT_DIR'
        PROJECT_ROOT='$PROJECT_ROOT'
        DEPLOY_DIR='$DEPLOY_DIR'
        AWS_REGION='$AWS_REGION'
        AWS_PROFILE='${AWS_PROFILE:-terraform-dev}'
        EC2_HOST='$EC2_HOST'
        source '$DEPLOY_DIR/common.sh'
        source '$DEPLOY_DIR/ami.sh'
        step_bake_ami
    " >> /tmp/ami-bake.log 2>&1 &
    # Record PID in lock file so a future deploy can kill this process if needed
    echo "PID=$!" > "$_AMI_LOCKFILE"
    disown $! 2>/dev/null || true  # detach from job control so this script can exit cleanly
fi
echo ""
# ─────────────────────────────────────────────────────────────────────────────

# ACCESS NOTE:
#
# Port 32147 (Dashboard) — open to the public internet via the security group in terraform/main.tf.
#   Accessible at: http://<ELASTIC_IP>:32147/dashboard/
#   Protected by HTTP Basic Auth (VALIDATION_USER / VALIDATION_PASS from K8s secrets).
#   If this port is not reachable, run: ./scripts/deploy.sh --provision
#   (runs terraform apply to sync the security group rules to AWS).
#
# Port 30080 (Airflow UI) — NOT publicly open; requires SSH tunnel:
#   ssh -L 30080:localhost:30080 -L 5500:localhost:5500 ec2-stock
#   Then open: http://localhost:30080 (Airflow)  or  http://localhost:5500 (MLflow)
