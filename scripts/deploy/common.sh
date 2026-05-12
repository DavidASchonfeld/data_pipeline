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
    local exit_code="${_DEPLOY_EXIT:-$?}"  # prefer exit code captured by the EXIT trap before any cleanup runs
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
    # -i: case-insensitive so "Error:" (Kafka), "Warning:" (pip), etc. are caught alongside all-caps variants
    local summary_lines
    summary_lines=$(grep -iE "(WARNING|ERROR|⚠|DeprecationWarning|DEPRECATION:|FutureWarning|UserWarning|✗)" \
        "$DEPLOY_LOGFILE" \
        | grep -v -- "--ignore-not-found" \
        | awk '!seen[$0]++') || true  # || true: if grep finds nothing it exits non-zero — this prevents that from stopping the script
    # Run spot detection now so SPOT_INTERRUPTION_DETECTED is known before the fallback below
    if [ "$exit_code" -ne 0 ] && [ "${DEPLOY_INTERRUPTED:-false}" != "true" ]; then
        _detect_spot_interruption
    fi
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
            if [ "${SPOT_INTERRUPTION_DETECTED:-false}" = "true" ]; then
                # Spot interruption explains the exit — suppressing log tail to avoid showing
                # unrelated in-flight output (e.g. pytest lines) as if they caused the failure
                echo "  (none — spot interruption explains exit; see diagnosis below)"
            else
                # No WARNING/ERROR keywords were found — show the last 15 log lines so there is
                # always a visible trail; this catches failures like SSH exit 255 that print nothing
                echo "  No WARNING/ERROR keywords found. Last 15 log lines:"
                echo ""
                tail -n 15 "$DEPLOY_LOGFILE" | while IFS= read -r line; do
                    echo "    > $line"
                done
                echo ""
                echo "  Script exited with errors — check items above and logs for details."
            fi
        else
            echo "  (none)"
            type _deploy_status_write_clean &>/dev/null && _deploy_status_write_clean  # call site 2 — log clean finish
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
    # On failure (not Ctrl+C), print spot diagnosis (detection already ran above)
    if [ "$exit_code" -ne 0 ] && [ "${DEPLOY_INTERRUPTED:-false}" != "true" ]; then
        echo "  -- Interruption Diagnosis --------------------------------------"
        if [ "${SPOT_INTERRUPTION_DETECTED:-false}" = "true" ]; then
            echo "  SPOT INTERRUPTION CONFIRMED"
            echo "  Reason:   $SPOT_TERMINATION_REASON"
            echo "  Instance: ${_EC2_INSTANCE_ID:-unknown}"
            echo "  ASG (min=1) is spinning up a replacement (~2-5 min)."
            echo "  Re-run:   ./scripts/deploy.sh ${DEPLOY_ORIG_ARGS[*]:-}"
            [ "${AUTO_RETRY_ON_SPOT:-false}" != "true" ] && \
                echo "  Tip:      add --auto-retry-on-spot to wait and retry automatically."
        else
            echo "  Not a spot interruption: $SPOT_TERMINATION_REASON"
        fi
        echo "  ---------------------------------------------------------------"
    fi
    echo "=================================================================="
    echo "  Full log: $DEPLOY_LOGFILE"
    echo "=================================================================="
    echo ""
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Disk-pressure helpers ─────────────────────────────────────────────────────
# Prune Docker + K3s images if disk > 75% — keeps the node below kubelet's eviction threshold.
# Also calls _remove_disk_pressure_taint so pod scheduling resumes immediately after cleanup.
_ensure_disk_space() {
    # BUILD_TAG is set by deploy.sh after the airflow build; falls back to a sentinel that won't
    # match any real tag, so a pre-build call won't accidentally delete the only airflow-dbt image.
    local _build_tag="${BUILD_TAG:-NEVER_MATCH}"
    ssh "$EC2_HOST" "
        DISK_USE=\$(df / | awk 'NR==2 {gsub(/%/,\"\",\$5); print \$5}')
        if [ \"\${DISK_USE:-0}\" -gt 75 ]; then
            echo \"Disk at \${DISK_USE}% — pruning to prevent disk-pressure / kubelet eviction...\"
            # Drop unused tagged airflow-dbt images from prior builds — these accumulate at ~5 GB each
            # and are invisible to 'docker system prune' (only removes dangling, not tagged-but-unused).
            # Skip the current build tag so the live image stays available for K3S re-import recovery.
            docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
                | grep -E '^airflow-dbt:3\.1\.8-dbt-' \
                | grep -v ':${_build_tag}\$' \
                | xargs -r docker rmi -f 2>/dev/null || true
            # buildx layer cache (no-op when empty, but cheap to call)
            docker buildx prune -af 2>/dev/null || true
            # Drop unused tagged images too (not just dangling) — frees Docker's containerd-backed layer store.
            # until=2h spares anything Docker may need for the in-flight build started this deploy.
            docker system prune -af --filter 'until=2h' 2>/dev/null || true
            # K3S has its own containerd image store separate from Docker's
            sudo k3s crictl rmi --prune 2>/dev/null || true
            # GC orphaned content blobs — k3s ctr images rm only removes the tag reference, not the layer bytes.
            # Each deploy without this leaves ~1.2 GB of orphaned layers that accumulate across builds.
            sudo k3s ctr content gc 2>/dev/null || true
            DISK_AFTER=\$(df / | awk 'NR==2 {gsub(/%/,\"\",\$5); print \$5}')
            echo \"Disk after prune: \${DISK_AFTER}%\"
            if [ \"\${DISK_AFTER:-0}\" -gt 80 ]; then
                # Cache prune wasn't enough — dominant usage is live container/PVC data.
                # Trigger at 80% (lowered from 85%) so the deeper sweep runs before the build/import peak,
                # which has historically pushed disk into the 87-90% range mid-deploy.
                echo \"Disk still at \${DISK_AFTER}% — running secondary cleanup (logs, journal)...\"
                echo '--- Top disk consumers ---'
                sudo du -sh /var/log/pods /var/lib/rancher/k3s/storage \
                    /var/lib/rancher/k3s/agent/containerd /var/lib/containerd /var/lib/docker 2>/dev/null || true
                echo '--------------------------'
                # Truncate pod logs older than 3 days — kubelet holds open handles so truncate (not rm) is safe
                find /var/log/pods -name '*.log' -mtime +3 -exec truncate -s 0 {} \; 2>/dev/null || true
                # Vacuum systemd journal — can quietly grow past 1 GB on this host
                sudo journalctl --vacuum-time=2d 2>/dev/null || true
                # Delete Evicted pod objects — each holds a /var/log/pods/<uid> directory on disk even after eviction.
                # 14 evicted MLflow pods were observed accumulating on May 8; deleting them releases the log dirs.
                # Scope to airflow-my-namespace — the only namespace where evicted pods accumulate
                # (MLflow, api-server, dag-processor). default holds only the Flask pod, which deploy.sh
                # manages explicitly; a global sweep race-deleted it on 2026-05-09.
                echo 'Deleting any Evicted pod objects in airflow-my-namespace to reclaim their log directories...'
                kubectl get pods -n airflow-my-namespace --no-headers 2>/dev/null \
                    | awk '\$3 == \"Evicted\" {print \$1}' \
                    | while read -r _pod; do
                        kubectl delete pod \"\$_pod\" -n airflow-my-namespace --grace-period=0 --force 2>/dev/null || true
                      done
                # Remove Released PVs — PVs in Released state hold storage but cannot be reattached to a claim.
                kubectl get pv --no-headers 2>/dev/null \
                    | awk '\$5 == \"Released\" {print \$1}' \
                    | xargs -r kubectl delete pv 2>/dev/null || true
                # Sweep orphaned local-path provisioner directories — kubectl delete pv only removes the cluster object,
                # not the underlying folder under /var/lib/rancher/k3s/storage/. Past helm upgrades have left
                # ~700 MB-1 GB of dead postgres+kafka dirs sitting around. The directory is root-owned so we
                # enumerate via sudo find. Skip the loop entirely if the live-PV lookup returns empty
                # (transient kubectl failure) — never delete data on a fluke.
                LIVE_PVS=\$(kubectl get pv -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\\n')
                if [ -n \"\$LIVE_PVS\" ]; then
                    sudo find /var/lib/rancher/k3s/storage -mindepth 1 -maxdepth 1 -type d -name 'pvc-*' 2>/dev/null \
                        | while read -r _dir; do
                            # Directory name format: pvc-<uid>_<namespace>_<pvc-name> — PV name is the leading pvc-<uid> part.
                            _pv_name=\$(basename \"\$_dir\" | awk -F_ '{print \$1}')
                            echo \"\$LIVE_PVS\" | grep -qFx \"\$_pv_name\" || { echo \"Removing orphan PV dir: \$(basename \"\$_dir\")\"; sudo rm -rf \"\$_dir\"; }
                          done
                fi
                # Remove orphaned pod log directories whose owning pod no longer exists.
                # Evicted pods leave their log dirs even after the pod object is deleted above.
                LIVE_UIDS=\$(kubectl get pods -A -o jsonpath='{.items[*].metadata.uid}' 2>/dev/null | tr ' ' '\n')
                for _dir in /var/log/pods/*/; do
                    _uid=\$(basename \"\$_dir\" | awk -F_ '{print \$NF}')
                    echo \"\$LIVE_UIDS\" | grep -qF \"\$_uid\" || sudo rm -rf \"\$_dir\" 2>/dev/null || true
                done
                # Sweep stale image-import tarballs only (>5 min old) — younger tars belong to a concurrent in-flight import.
                sudo find /tmp -maxdepth 1 -name 'k3s-import-*.tar' -mmin +5 -delete 2>/dev/null || true
                # Clean up Ubuntu's package download cache and remove old kernel backups Ubuntu kept as fallbacks.
                # apt-get autoremove --purge only removes kernels that are not currently running — it is safe.
                sudo apt-get clean 2>/dev/null || true
                sudo DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y 2>&1 | tail -3 || true
                # Tell snap to keep only 2 old versions of each package, then remove anything already superseded.
                # snap only marks a revision 'disabled' after it has been fully replaced — removing them is safe.
                sudo snap set system refresh.retain=2 2>/dev/null || true
                snap list --all 2>/dev/null | awk '/disabled/{print \$1, \$3}' | while read -r _snapname _snaprev; do
                    sudo snap remove \"\$_snapname\" --revision=\"\$_snaprev\" 2>/dev/null || true
                done
                # Empty pip's download cache — pip just re-downloads from the internet next time it needs a package.
                sudo rm -rf /root/.cache/pip /home/ubuntu/.cache/pip 2>/dev/null || true
                DISK_AFTER=\$(df / | awk 'NR==2 {gsub(/%/,\"\",\$5); print \$5}')
                echo \"Disk after secondary cleanup: \${DISK_AFTER}%\"
                if [ \"\${DISK_AFTER:-0}\" -gt 92 ]; then
                    # Abort rather than risk kubelet evicting pods mid-deploy (what caused the May 8 Flask eviction).
                    # Threshold raised from 90 → 92 since live PVC/container baseline is genuinely high on this host.
                    echo \"ERROR: disk at \${DISK_AFTER}% after full cleanup — live PVC/container data too large. Aborting deploy.\"
                    exit 1
                elif [ \"\${DISK_AFTER:-0}\" -gt 85 ]; then
                    # Detailed breakdown — secondary cleanup ran but disk is still high, so the remainder is live data.
                    # Print which PVCs, images, and log dirs are the actual consumers so the operator can decide what to trim.
                    echo ''
                    echo '=== Detailed disk breakdown (live data still > 85%) ==='
                    echo '-- hostPath PVC bind mounts --'
                    sudo du -sh \\
                        /home/ubuntu/airflow/logs \\
                        /home/ubuntu/airflow/dags \\
                        /home/ubuntu/airflow/dag-mylogs \\
                        /home/ubuntu/mlflow-data \\
                        2>/dev/null || true
                    echo '-- K3s local-path provisioner volumes (PostgreSQL, Kafka, etc.) --'
                    sudo du -sh /var/lib/rancher/k3s/storage/* 2>/dev/null || true
                    echo '-- K3s containerd images: top 5 by size --'
                    sudo k3s crictl images 2>/dev/null | awk 'NR>1' | sort -k5 -h -r | head -5 || true
                    echo '-- K3s containerd images: pinned count --'
                    PINNED=0
                    for _img in \$(sudo k3s ctr images ls -q 2>/dev/null); do
                        sudo k3s ctr images label \"\$_img\" 2>/dev/null \
                            | grep -q 'io.cri-containerd.pinned=pinned' && PINNED=\$(( PINNED + 1 ))
                    done
                    echo \"Pinned images: \$PINNED\"
                    echo '-- /var/log/pods total + orphan count --'
                    sudo du -sh /var/log/pods 2>/dev/null || true
                    LIVE_UIDS=\$(kubectl get pods -A -o jsonpath='{.items[*].metadata.uid}' 2>/dev/null | tr ' ' '\\n')
                    POD_TOTAL=0; POD_ORPHAN=0
                    for _dir in /var/log/pods/*/; do
                        [ -d \"\$_dir\" ] || continue
                        POD_TOTAL=\$(( POD_TOTAL + 1 ))
                        _uid=\$(basename \"\$_dir\" | awk -F_ '{print \$NF}')
                        echo \"\$LIVE_UIDS\" | grep -qF \"\$_uid\" || POD_ORPHAN=\$(( POD_ORPHAN + 1 ))
                    done
                    echo \"Total pod log dirs: \$POD_TOTAL | orphaned (no live pod): \$POD_ORPHAN\"
                    echo '-- Top 10 files >100 MB on root volume --'
                    sudo find / -xdev -type f -size +100M -exec ls -lh {} \\; 2>/dev/null \
                        | awk '{printf \"%-8s %s\\n\", \$5, \$9}' | sort -h -r | head -10 || true
                    echo '======================================================='
                    echo ''
                    echo \"WARNING: disk still at \${DISK_AFTER}% after prune — likely live container/PVC usage, not cache\"
                fi
            fi
        else
            echo \"Disk at \${DISK_USE}% — OK\"
        fi
    "
    _remove_disk_pressure_taint
}

# Remove disk-pressure:NoSchedule taint from the ready node after disk cleanup.
# K3s adds this taint automatically when disk > 85% — removing it here unblocks pod scheduling
# immediately instead of waiting for K3s's next node-condition sync cycle (~30s).
_remove_disk_pressure_taint() {
    ssh "$EC2_HOST" "
        NODE=\$(kubectl get nodes --no-headers 2>/dev/null | awk '\$2 == \"Ready\" {print \$1}' | head -1)
        [ -z \"\$NODE\" ] && exit 0
        if kubectl get node \"\$NODE\" -o jsonpath='{.spec.taints[*].key}' 2>/dev/null \
            | grep -qw 'node.kubernetes.io/disk-pressure'; then
            echo \"Removing disk-pressure taint from node \$NODE...\"
            kubectl taint node \"\$NODE\" node.kubernetes.io/disk-pressure:NoSchedule- 2>/dev/null || true
            echo 'Disk-pressure taint removed.'
        else
            echo 'No disk-pressure taint — OK'
        fi
    "
}
# Print disk, Docker, and K3s image usage at deploy start — informational only, does not gate the deploy.
# Use the output over several deploys to decide whether to resize the EBS volume or tune image strategy.
_log_disk_diagnostics() {
    echo "=== Disk diagnostics (informational) ==="
    ssh "$EC2_HOST" "
        echo '-- Root filesystem --'
        df -h /
        echo '-- Docker layer/cache usage --'
        docker system df 2>/dev/null || echo '(docker not available)'
        echo '-- K3s containerd: image count --'
        sudo k3s crictl images --quiet 2>/dev/null | wc -l || echo '(k3s not available)'
        echo '-- Image-store sizes (K3s vs Docker — should not both be large) --'
        sudo du -sh /var/lib/rancher/k3s/agent/containerd /var/lib/containerd /var/lib/docker 2>/dev/null || echo '(path not found)'
        echo '-- Top 5 largest K3s images --'
        sudo k3s crictl images 2>/dev/null | awk 'NR>1 {print}' | sort -k5 -h -r | head -5 || true
    "
    echo "========================================"
    # Detailed breakdown: PVCs, pod logs, swapfile — captures drift across deploys
    _log_disk_breakdown
}
# ─────────────────────────────────────────────────────────────────────────────

# ── K8s and K3S helpers ───────────────────────────────────────────────────────
# Pipe a Docker image into K3S containerd on EC2; grep_term is the string used to verify it was imported.
# Avoids writing a temporary tar file — pipes directly to k3s ctr images import (saves disk space on EC2).
# Retries once with a short delay — concurrent K3s containerd writes from parallel background jobs can
# produce a transient "failed commit on ref" race condition on the first attempt.
import_image_to_k3s() {
    local image_name="$1" grep_term="$2"
    # 5 retries with 15s delay — containerd lease errors on large images need more breathing room
    for _attempt in 1 2 3 4 5; do
        if ssh "$EC2_HOST" "
            echo 'Importing $image_name into K3S containerd (attempt $_attempt/5)...' &&
            # Clear stale containerd leases before import — leftover leases from interrupted imports
            # cause 'lease does not exist: not found' when a new import tries to claim the same content
            sudo k3s ctr leases ls -q 2>/dev/null | xargs -r sudo k3s ctr leases delete 2>/dev/null || true &&
            # Save to a temp tar first — piping docker save directly to ctr import truncates large images
            # (ctr reports 'short read: expected N bytes but got M') because the SSH pipe can drop data mid-stream
            _tmp_tar=\$(mktemp /tmp/k3s-import-XXXXXX.tar) &&
            docker save '$image_name' > \"\$_tmp_tar\" &&
            sudo k3s ctr images import \"\$_tmp_tar\" &&
            rm -f \"\$_tmp_tar\" &&
            echo 'Verifying image is visible to K3S...' &&
            sudo k3s ctr images ls | grep '$grep_term'
        "; then
            return 0
        fi
        if [ "$_attempt" -lt 5 ]; then
            echo "K3S import attempt $_attempt failed, retrying in 15s..."
            # Clean up only stale tars (>5 min old) — younger tars may belong to a parallel in-flight import.
            ssh "$EC2_HOST" "find /tmp -maxdepth 1 -name 'k3s-import-*.tar' -mmin +5 -delete 2>/dev/null || true"
            # If containerd's socket was reset (happens under heavy parallel load), verify the socket
            # is responsive before retrying — a non-responsive socket means the import will fail again immediately
            if ssh "$EC2_HOST" "sudo k3s ctr version >/dev/null 2>&1"; then
                sleep 15  # socket is fine — brief pause is enough
            else
                echo "K3S containerd socket unresponsive — waiting 30s for it to recover..."
                sleep 30  # socket is down — give it more time before retrying
            fi
        fi
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
# and start sshd, so we retry before giving up.
# Pass an attempt count: default 36 (6 min); use 90 (15 min) after --provision since
# fresh instances run a full user-data bootstrap (Docker, K3s, apt) before sshd starts.
# StrictHostKeyChecking=accept-new: automatically trusts a brand-new host key (safe after
# deploy.sh clears known_hosts for the replaced instance) but still rejects unexpected
# key changes on already-known hosts.
_wait_ssh_ready() {
    local _max="${1:-36}"  # attempts × 10s; default 6 min
    local _mins=$(( _max * 10 / 60 ))
    for _attempt in $(seq 1 "$_max"); do
        if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$EC2_HOST" true 2>/dev/null; then
            return 0
        fi
        echo "SSH not ready (attempt $_attempt/$_max), retrying in 10s..."
        [ "$_attempt" -lt "$_max" ] && sleep 10
    done
    echo "✗ EC2 SSH unreachable after $_max attempts (${_mins} min)"
    return 1
}

# ── SSH pre-flight check ──────────────────────────────────────────────────────
# Before waiting 6 minutes, quickly detect whether the security group is blocking
# SSH because the deployer's public IP changed since the last terraform apply.
# Fails fast with an actionable message instead of silently timing out.
# Skips silently if aws CLI or credentials are unavailable (graceful degradation).
_check_ssh_prereqs() {
    local _profile="${AWS_PROFILE:-terraform-dev}"
    # Require aws CLI and a valid session — skip check if either is missing
    if ! command -v aws &>/dev/null; then return 0; fi
    if ! aws sts get-caller-identity --profile "$_profile" &>/dev/null; then return 0; fi

    # Fetch the SSH ingress CIDR from the pipeline security group
    local _sg_cidr
    _sg_cidr=$(aws ec2 describe-security-groups \
        --profile "$_profile" --region "$AWS_REGION" \
        --filters "Name=tag:Name,Values=pipeline-sg" \
        --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\`].IpRanges[0].CidrIp" \
        --output text 2>/dev/null)

    if [ -z "$_sg_cidr" ] || [ "$_sg_cidr" = "None" ]; then return 0; fi  # can't determine SG — proceed

    # Get current public IP
    local _my_ip
    _my_ip=$(curl -fsSL --max-time 5 ifconfig.me 2>/dev/null)
    if [ -z "$_my_ip" ]; then return 0; fi  # can't reach ifconfig.me — proceed

    local _my_cidr="${_my_ip}/32"
    if [ "$_my_cidr" != "$_sg_cidr" ]; then
        echo "✗ Security group SSH rule allows ${_sg_cidr} but your current IP is ${_my_ip}"
        echo "  Run:  ./scripts/deploy.sh --provision   (updates the security group and retries SSH)"
        return 1
    fi
}
# ─────────────────────────────────────────────────────────────────────────────

# ── K3s node readiness helper ─────────────────────────────────────────────────
# SSH can succeed 2-3 min before K3s finishes starting — without this wait,
# Helm and Kafka apply against a NotReady node and pods get stuck in Pending.
_wait_k3s_ready() {
    echo "=== Waiting for K3s node to be Ready (up to 5 minutes) ==="
    # 30 attempts × 10s = 5 min — matches the wait in bootstrap.sh and user-data.sh.tpl
    for _attempt in $(seq 1 30); do
        if ssh -o ConnectTimeout=5 "$EC2_HOST" "kubectl get nodes 2>/dev/null | grep -q ' Ready'"; then
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

# ── K3s API server readiness helper ──────────────────────────────────────────
# kubectl apply fetches /openapi/v2 before doing anything — that endpoint
# initializes later than kubectl get nodes, so we verify it explicitly.
# /healthz returns "ok" when the API server is fully ready.
_wait_k3s_api_ready() {
    echo "=== Waiting for K3s API server to be ready (up to 6 minutes) ==="
    # 36 attempts × 10s = 6 min; uses kubectl's own kubeconfig so no curl/TLS issues
    for _attempt in $(seq 1 36); do
        if ssh -o ConnectTimeout=5 "$EC2_HOST" "kubectl get --raw /healthz 2>/dev/null | grep -q 'ok'"; then
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
# ─────────────────────────────────────────────────────────────────────────────

# ── Spot interruption detection ───────────────────────────────────────────────
# Grab instance ID + public IP from EC2's metadata service right after SSH is confirmed ready.
# Stored as globals so _detect_spot_interruption can query AWS even after SSH has dropped.
_capture_instance_metadata() {
    # IMDSv2: get a short-lived token first, then use it to fetch instance metadata
    _EC2_INSTANCE_ID=$(ssh "$EC2_HOST" "
        TOKEN=\$(curl -sXPUT 'http://169.254.169.254/latest/api/token' \
            -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600' --max-time 5 2>/dev/null)
        curl -sf --max-time 5 -H \"X-aws-ec2-metadata-token: \$TOKEN\" \
            http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null
    " 2>/dev/null || echo "")
    _EC2_PUBLIC_IP=$(ssh "$EC2_HOST" "
        TOKEN=\$(curl -sXPUT 'http://169.254.169.254/latest/api/token' \
            -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600' --max-time 5 2>/dev/null)
        curl -sf --max-time 5 -H \"X-aws-ec2-metadata-token: \$TOKEN\" \
            http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null
    " 2>/dev/null || echo "")
    export _EC2_INSTANCE_ID _EC2_PUBLIC_IP
    # only print if I got an ID — an empty result here should not stop the deploy
    if [ -n "$_EC2_INSTANCE_ID" ]; then
        echo "Instance metadata captured: $_EC2_INSTANCE_ID  ($_EC2_PUBLIC_IP)"
    fi
}

# Query AWS after an SSH failure to determine if a spot interruption caused it.
# Sets SPOT_INTERRUPTION_DETECTED=true/false and SPOT_TERMINATION_REASON (plain-English string).
# Uses subshell-safe patterns (|| echo "") so AWS CLI failures never abort the EXIT trap.
_detect_spot_interruption() {
    SPOT_INTERRUPTION_DETECTED=false
    SPOT_TERMINATION_REASON="unknown"

    # No instance ID means SSH was never ready — can't diagnose
    if [ -z "${_EC2_INSTANCE_ID:-}" ]; then
        SPOT_TERMINATION_REASON="no instance ID captured (SSH may never have connected)"
        return
    fi

    local _profile="${AWS_PROFILE:-terraform-dev}"

    local _state _lifecycle
    _state=$(aws ec2 describe-instances --profile "$_profile" --region "$AWS_REGION" \
        --instance-ids "$_EC2_INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo "")
    _lifecycle=$(aws ec2 describe-instances --profile "$_profile" --region "$AWS_REGION" \
        --instance-ids "$_EC2_INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].InstanceLifecycle' --output text 2>/dev/null || echo "")

    if [ -z "$_state" ] || [ "$_state" = "None" ] || [ "$_state" = "null" ]; then
        SPOT_TERMINATION_REASON="could not query AWS (check credentials or region)"
        return
    fi

    # Instance still running — SSH dropped for a non-spot reason (OOM, network blip, script bug)
    if [ "$_state" = "running" ]; then
        SPOT_TERMINATION_REASON="instance $_EC2_INSTANCE_ID is still running — likely OOM, network blip, or script bug (not a spot event)"
        return
    fi

    # Instance is gone but it is not a spot instance
    if [ "$_lifecycle" != "spot" ]; then
        SPOT_TERMINATION_REASON="instance $_EC2_INSTANCE_ID terminated (state: $_state) but is not a spot instance"
        return
    fi

    # Spot instance terminated — look up the specific AWS status code
    local _spot_req_id _spot_code
    _spot_req_id=$(aws ec2 describe-instances --profile "$_profile" --region "$AWS_REGION" \
        --instance-ids "$_EC2_INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].SpotInstanceRequestId' --output text 2>/dev/null || echo "")

    if [ -n "$_spot_req_id" ] && [ "$_spot_req_id" != "None" ]; then
        _spot_code=$(aws ec2 describe-spot-instance-requests --profile "$_profile" --region "$AWS_REGION" \
            --spot-instance-request-ids "$_spot_req_id" \
            --query 'SpotInstanceRequests[0].Status.Code' --output text 2>/dev/null || echo "")
    fi

    # Map AWS status codes to plain-English reasons
    case "${_spot_code:-}" in
        instance-terminated-no-capacity) SPOT_TERMINATION_REASON="AWS reclaimed capacity (no capacity available in AZ)" ;;
        instance-terminated-by-price)    SPOT_TERMINATION_REASON="spot price exceeded your bid" ;;
        instance-terminated-by-user)     SPOT_TERMINATION_REASON="terminated manually (by user or automation)" ;;
        instance-terminated-by-schedule) SPOT_TERMINATION_REASON="terminated by schedule" ;;
        marked-for-termination)          SPOT_TERMINATION_REASON="instance was marked for termination (2-min warning issued)" ;;
        "")                              SPOT_TERMINATION_REASON="spot instance terminated (reason unavailable)" ;;
        *)                               SPOT_TERMINATION_REASON="spot instance terminated (AWS code: $_spot_code)" ;;
    esac

    SPOT_INTERRUPTION_DETECTED=true
}

# If --auto-retry-on-spot was passed and a spot interruption is confirmed, wait for the
# replacement instance (ASG min=1 will spin one up) then re-exec with the original flags.
_maybe_spot_retry() {
    [ "${SPOT_INTERRUPTION_DETECTED:-false}" = "true" ] || return 0
    [ "${AUTO_RETRY_ON_SPOT:-false}" = "true" ] || return 0
    echo ""
    echo "=== Auto-retry: waiting for replacement instance (up to 15 min) ==="
    if _wait_ssh_ready 90; then
        echo "=== Replacement instance ready — re-running deploy ==="
        exec "$0" "${DEPLOY_ORIG_ARGS[@]}"
    else
        echo "✗ Replacement instance not ready after 15 min — re-run manually:"
        echo "  ./scripts/deploy.sh ${DEPLOY_ORIG_ARGS[*]:-}"
    fi
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Background job error helper ───────────────────────────────────────────────
# _wait_bg PID label — waits for a background job to finish, then prints success or failure and exits if it failed.
# WHY this is needed: bash's 'stop on error' setting does not apply to background jobs (&).
# Without this function, a failed background SSH job would disappear silently and the script
# would keep running as if nothing went wrong. This function catches that.
_wait_bg() {
    local pid=$1 label=$2
    local _start=$SECONDS
    # Print before waiting so the terminal doesn't appear frozen during long background jobs
    echo "Waiting for $label..."
    # Heartbeat: print elapsed time every 60s so the terminal is never silent for more than a minute
    ( while kill -0 "$pid" 2>/dev/null; do
        sleep 60
        kill -0 "$pid" 2>/dev/null && echo "  ... $label still running ($(( SECONDS - _start ))s elapsed)"
    done ) &
    local _hb_pid=$!
    if wait "$pid"; then
        kill "$_hb_pid" 2>/dev/null; wait "$_hb_pid" 2>/dev/null || true
        echo "✓ $label done ($(( SECONDS - _start ))s)"
    else
        kill "$_hb_pid" 2>/dev/null; wait "$_hb_pid" 2>/dev/null || true
        echo "✗ $label FAILED"
        exit 1
    fi
}

# Re-applies read permission on the K3s config file — K3s resets it to root-only whenever
# the k3s service restarts (e.g., due to memory/IO pressure during heavy parallel builds).
# Called before any kubectl commands in background jobs to ensure they can actually connect.
_ensure_kubectl_accessible() {
    ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml 2>/dev/null || true"
}
# ─────────────────────────────────────────────────────────────────────────────

# Instance is always-on (spot) — no sleep/wake helpers needed.
# SSH readiness is checked directly in deploy.sh before each deploy.
