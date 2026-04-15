#!/bin/bash
# Module: ami — bakes a golden AMI from the running instance for fast future boots.
# Can be sourced by deploy.sh or called directly: ./scripts/deploy/ami.sh bake|status

# If sourced by deploy.sh, SCRIPT_DIR etc. are already set; if called directly, set them up
if [ -z "${SCRIPT_DIR:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
    DEPLOY_DIR="$SCRIPT_DIR"
    source "$DEPLOY_DIR/common.sh"
fi

# Use the same AWS SSO profile as terraform.sh for all AWS CLI calls
export AWS_PROFILE="${AWS_PROFILE:-terraform-dev}"

# Prefix for AMI names so we can find and clean up old ones
AMI_NAME_PREFIX="pipeline-golden"
# Lock file shared between deploy.sh and ami.sh to coordinate background bakes
_AMI_LOCKFILE="/tmp/ami-bake.lock"

# Check if the AWS session is active; if not, open the browser so the user can log in.
# Called at the start of every step that talks to AWS so errors are clear instead of silent.
_ensure_aws_auth() {
    # Try a lightweight AWS call — if it succeeds, we are already logged in and can continue
    if aws sts get-caller-identity >/dev/null 2>&1; then
        return 0
    fi

    # Session is expired — prompt the user to log in via their web browser
    echo "--- AWS session expired. Opening browser for SSO login... ---"
    # This command opens a browser tab where the user clicks Allow to grant access
    if ! aws sso login --profile "$AWS_PROFILE"; then
        # Login was not completed (browser closed, timed out, or cancelled)
        echo "ERROR: AWS SSO login did not complete."
        echo "  Try running manually: aws sso login --profile $AWS_PROFILE"
        return 1
    fi

    # Confirm the login actually worked before letting the rest of the script continue
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        # Login appeared to succeed but AWS still rejects us — something is misconfigured
        echo "ERROR: AWS authentication failed even after SSO login."
        echo "  Try running manually: aws sso login --profile $AWS_PROFILE"
        return 1
    fi

    echo "--- AWS authentication confirmed ---"
}

# Cancel a previous in-progress AMI bake so we can start a fresh one with the latest code.
# Reads PID and AMI ID from the lock file, kills the old process, deregisters the pending AMI,
# deletes the backing snapshot, and ensures EC2 services are running.
cancel_in_progress_bake() {
    # Exit early if there is no lock file (nothing to cancel)
    if [ ! -f "$_AMI_LOCKFILE" ]; then
        return 0
    fi

    echo "--- Reading previous bake info from lock file ---"
    # Parse PID and AMI ID from the lock file (format: PID=<pid> and AMI=<ami-id>)
    local _old_pid _old_ami
    _old_pid=$(grep '^PID=' "$_AMI_LOCKFILE" 2>/dev/null | cut -d= -f2)
    _old_ami=$(grep '^AMI=' "$_AMI_LOCKFILE" 2>/dev/null | cut -d= -f2)

    # 1. Kill the old background bake process if it is still running
    if [ -n "$_old_pid" ] && kill -0 "$_old_pid" 2>/dev/null; then
        # Verify the PID belongs to an AMI bake process (prevents killing a recycled PID)
        local _proc_cmd
        _proc_cmd=$(ps -p "$_old_pid" -o command= 2>/dev/null || echo "")
        if echo "$_proc_cmd" | grep -qE "(ami|bake)"; then
            echo "Killing previous bake process (PID $_old_pid)..."
            pkill -TERM -P "$_old_pid" 2>/dev/null || true   # kill child processes first (SSH sessions)
            kill -TERM "$_old_pid" 2>/dev/null || true        # then kill the parent shell
            sleep 1                                           # brief wait for process cleanup
        else
            echo "PID $_old_pid is not a bake process — skipping kill (PID was recycled)"
        fi
    fi

    # 2. Deregister the pending/completed AMI from the previous bake
    if [ -n "$_old_ami" ]; then
        local _ami_state
        _ami_state=$(aws ec2 describe-images --image-ids "$_old_ami" --region "$AWS_REGION" \
            --query 'Images[0].State' --output text 2>/dev/null || echo "not-found")

        if [ "$_ami_state" != "not-found" ] && [ "$_ami_state" != "None" ]; then
            echo "Deregistering old AMI $_old_ami (state: $_ami_state)..."

            # Find the snapshot backing this AMI so we can delete it too (saves storage costs)
            local _snap_id
            _snap_id=$(aws ec2 describe-images --image-ids "$_old_ami" --region "$AWS_REGION" \
                --query 'Images[0].BlockDeviceMappings[0].Ebs.SnapshotId' --output text 2>/dev/null || echo "")

            # Deregister the AMI (cancels pending snapshot if still in progress)
            aws ec2 deregister-image --image-id "$_old_ami" --region "$AWS_REGION" 2>/dev/null || true

            # If primary lookup missed the snapshot, search by AMI ID in snapshot description
            if [ -z "$_snap_id" ] || [ "$_snap_id" = "None" ]; then
                _snap_id=$(aws ec2 describe-snapshots --owner-ids self \
                    --filters "Name=description,Values=*$_old_ami*" \
                    --region "$AWS_REGION" \
                    --query 'Snapshots[0].SnapshotId' --output text 2>/dev/null || echo "")
            fi

            # Delete the backing snapshot to stop paying for its storage
            if [ -n "$_snap_id" ] && [ "$_snap_id" != "None" ]; then
                echo "Deleting old snapshot: $_snap_id"
                aws ec2 delete-snapshot --snapshot-id "$_snap_id" --region "$AWS_REGION" 2>/dev/null || true
            fi
        else
            echo "Previous AMI $_old_ami no longer exists — nothing to deregister"
        fi
    fi

    # 3. Ensure EC2 services are running (in case the kill happened while they were stopped)
    echo "--- Ensuring EC2 services are running ---"
    ssh "$EC2_HOST" "sudo systemctl start docker && sudo systemctl start k3s && echo 'Services confirmed running'" \
        2>/dev/null || echo "  (EC2 unreachable — services will start on next boot)"

    # 4. Clean up lock file and bake log so the new bake starts fresh
    rm -f "$_AMI_LOCKFILE"
    > /tmp/ami-bake.log   # truncate previous bake log

    echo "--- Previous bake cancelled successfully ---"
}

step_bake_ami() {
    echo "=== Baking Golden AMI ==="

    # Make sure we are logged in to AWS before doing anything — stops silent failures early
    _ensure_aws_auth || return 1

    # 1. Get the instance ID from the ASG (we need it to create the AMI)
    echo "--- Looking up instance ID from ASG ---"
    local instance_id
    instance_id=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names pipeline-asg \
        --region "$AWS_REGION" \
        --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

    if [ -z "$instance_id" ] || [ "$instance_id" = "None" ]; then
        echo "ERROR: No running instance found in ASG. Wake the instance first with --wake."
        return 1
    fi
    echo "Instance ID: $instance_id"

    # 2. Verify SSH works before we start
    echo "--- Verifying SSH connectivity ---"
    ssh -o ConnectTimeout=5 "$EC2_HOST" "echo 'SSH OK'" || {
        echo "ERROR: Cannot SSH to $EC2_HOST. Is the instance running?"
        return 1
    }

    # 3. Clean up temporary files to make the AMI smaller and cheaper to store
    echo "--- Cleaning temporary files (before stopping services) ---"
    ssh "$EC2_HOST" bash -s <<'CLEANUP'
        # Remove Docker build cache while Docker is still running (will be rebuilt on next deploy)
        sudo docker system prune -af 2>/dev/null || true
        # Truncate log files so the AMI does not carry old logs
        sudo truncate -s 0 /var/log/syslog 2>/dev/null || true
        sudo truncate -s 0 /var/log/pipeline-boot.log 2>/dev/null || true
        # Remove apt cache to save space
        sudo apt-get clean 2>/dev/null || true
        echo "Cleanup done"
CLEANUP

    # 3b. Reset K3s cluster state so new instance boots with a clean node and no stale pods
    # Without this, the AMI snapshot retains old node/pod entries in K3s's SQLite DB; when a new
    # instance boots with a different internal IP it registers as a second node and pods remain
    # pinned to the old (NotReady) node, leaving the dashboard unreachable after every wake cycle.
    echo "--- Resetting K3s cluster state before snapshot ---"
    ssh "$EC2_HOST" bash -s <<'RESET_K3S'
        # Force-delete all pods so none persist pinned to the old node IP in the SQLite snapshot
        kubectl delete pods --all --all-namespaces --grace-period=0 --force 2>/dev/null || true
        # Remove this node from K3s etcd; stop K3s immediately to prevent kubelet re-registration
        NODE=$(kubectl get nodes --no-headers -o custom-columns=':.metadata.name' 2>/dev/null | head -1)
        [ -n "$NODE" ] && kubectl delete node "$NODE" 2>/dev/null || true
        sudo systemctl stop k3s
        echo "K3s state reset: $(date -u)"
RESET_K3S

    # 4. Stop Docker gracefully so the disk is in a clean state for the snapshot
    # (K3s is already stopped by the reset step above — stopping it again is a safe no-op)
    echo "--- Stopping services for clean snapshot ---"
    ssh "$EC2_HOST" "sudo systemctl stop k3s 2>/dev/null || true && echo 'K3s stopped'"
    ssh "$EC2_HOST" "sudo systemctl stop docker && echo 'Docker stopped'"

    # 5. Create the AMI — this takes a snapshot of the entire disk
    local ami_name="${AMI_NAME_PREFIX}-$(date +%Y%m%d-%H%M%S)"
    echo "--- Creating AMI: $ami_name ---"
    local ami_id
    ami_id=$(aws ec2 create-image \
        --instance-id "$instance_id" \
        --name "$ami_name" \
        --no-reboot \
        --description "Pre-baked pipeline AMI with K3s, Airflow, Kafka, MLflow, and Flask dashboard" \
        --tag-specifications "ResourceType=image,Tags=[{Key=Name,Value=$ami_name},{Key=Project,Value=data-pipeline}]" \
        --region "$AWS_REGION" \
        --output text)
    # --no-reboot: we already stopped K3s/Docker above for a clean disk state, so AWS does not need to reboot too
    echo "AMI creation started: $ami_id"

    # Record AMI ID in lock file so a future deploy can cancel this pending AMI if needed
    if [ -f "$_AMI_LOCKFILE" ]; then
        echo "AMI=$ami_id" >> "$_AMI_LOCKFILE"
    fi

    # 6. Restart services while the AMI bakes (instance is still usable during snapshot)
    echo "--- Restarting services (instance stays usable while AMI bakes) ---"
    ssh "$EC2_HOST" "sudo systemctl start docker && sudo systemctl start k3s && echo 'Services restarted'"

    # 6b. Re-apply the Flask bare pod — K3s state was wiped in step 3b so bare pods don't auto-recreate
    # Deployments/StatefulSets (Airflow, MLflow) reschedule themselves; bare pods must be re-applied manually
    echo "--- Re-applying Flask pod after K3s restart ---"
    ssh "$EC2_HOST" bash -s <<'APPLY_FLASK'
        # Fix kubeconfig read permissions (K3s resets them to 600 on restart)
        sudo chmod 644 /etc/rancher/k3s/k3s.yaml 2>/dev/null || true
        # Wait for the node to register before trying to schedule pods
        for i in $(seq 1 18); do
            if kubectl get nodes 2>/dev/null | grep -q ' Ready'; then
                echo "Node ready (attempt $i)"
                break
            fi
            echo "Waiting for node ready (attempt $i/18)..."
            sleep 10
        done
        # Look up the ECR base registry URL from AWS (avoids hardcoding the account ID)
        ECR=$(aws ecr describe-repositories --repository-names my-flask-app \
            --query 'repositories[0].repositoryUri' --output text 2>/dev/null | cut -d/ -f1)
        if [ -n "$ECR" ] && [ -f /home/ubuntu/dashboard/manifests/pod-flask.yaml ]; then
            sed "s|\${ECR_REGISTRY}|$ECR|g" /home/ubuntu/dashboard/manifests/pod-flask.yaml \
                | kubectl apply -n default -f -
            echo "Flask pod applied (ECR: $ECR)"
        else
            echo "WARNING: Could not apply Flask pod — ECR=$ECR, manifest exists: $(test -f /home/ubuntu/dashboard/manifests/pod-flask.yaml && echo yes || echo no)"
        fi
APPLY_FLASK

    # 7. Wait for the AMI to finish baking (large 30GB snapshots can take up to 20+ minutes)
    echo "--- Waiting for AMI to become available (large snapshots can take 15-25 minutes) ---"
    # Use a manual polling loop since the default waiter only waits 10 minutes
    for _wait_attempt in $(seq 1 90); do
        _state=$(aws ec2 describe-images --image-ids "$ami_id" --region "$AWS_REGION" \
            --query 'Images[0].State' --output text 2>/dev/null || echo "unknown")
        if [ "$_state" = "available" ]; then
            echo "AMI is available after attempt $_wait_attempt"
            break
        fi
        echo "AMI state: $_state (attempt $_wait_attempt/90, polling every 20s)..."
        [ "$_wait_attempt" -lt 90 ] && sleep 20
    done
    _final_state=$(aws ec2 describe-images --image-ids "$ami_id" --region "$AWS_REGION" \
        --query 'Images[0].State' --output text 2>/dev/null || echo "unknown")
    if [ "$_final_state" != "available" ]; then
        echo "WARNING: AMI $ami_id state is '$_final_state' after polling — it may still be pending."
        echo "  Check state with: aws ec2 describe-images --image-ids $ami_id"
        echo "  The launch template has been updated and will use this AMI once it becomes available."
    fi
    echo "AMI is now available: $ami_id"

    # 8. Update the ASG launch template to use the new AMI for future boots
    echo "--- Updating launch template with new AMI ---"
    local lt_id
    lt_id=$(aws ec2 describe-launch-templates \
        --filters "Name=tag:Project,Values=data-pipeline" \
        --region "$AWS_REGION" \
        --query 'LaunchTemplates[0].LaunchTemplateId' --output text)

    if [ -z "$lt_id" ] || [ "$lt_id" = "None" ]; then
        echo "WARNING: Could not find launch template by tag. Trying by name..."
        lt_id=$(aws ec2 describe-launch-templates \
            --launch-template-names "pipeline-*" \
            --region "$AWS_REGION" \
            --query 'LaunchTemplates[0].LaunchTemplateId' --output text 2>/dev/null || echo "")
    fi

    if [ -n "$lt_id" ] && [ "$lt_id" != "None" ]; then
        # Create a new version of the launch template that uses the new AMI
        aws ec2 create-launch-template-version \
            --launch-template-id "$lt_id" \
            --source-version '$Latest' \
            --launch-template-data "{\"ImageId\":\"$ami_id\"}" \
            --region "$AWS_REGION" > /dev/null
        echo "Launch template $lt_id updated to use AMI $ami_id"
    else
        echo "WARNING: Could not find launch template. Update it manually with AMI $ami_id"
    fi

    # 9. Clean up old AMIs to avoid paying for snapshot storage on outdated images
    echo "--- Cleaning up old AMIs (keeping only the latest) ---"
    local old_amis
    old_amis=$(aws ec2 describe-images \
        --owners self \
        --filters "Name=name,Values=${AMI_NAME_PREFIX}-*" \
        --region "$AWS_REGION" \
        --query "Images[?ImageId!='$ami_id'].ImageId" --output text)

    for old_ami in $old_amis; do
        echo "Deregistering old AMI: $old_ami"
        # Find and delete the snapshot that backs the old AMI (snapshots cost money too)
        local snap_id
        snap_id=$(aws ec2 describe-images \
            --image-ids "$old_ami" \
            --region "$AWS_REGION" \
            --query 'Images[0].BlockDeviceMappings[0].Ebs.SnapshotId' --output text 2>/dev/null || echo "")
        aws ec2 deregister-image --image-id "$old_ami" --region "$AWS_REGION" 2>/dev/null || true
        if [ -n "$snap_id" ] && [ "$snap_id" != "None" ]; then
            echo "Deleting old snapshot: $snap_id"
            aws ec2 delete-snapshot --snapshot-id "$snap_id" --region "$AWS_REGION" 2>/dev/null || true
        fi
    done

    # 10. Clean up old launch template versions so they don't accumulate forever (keep the 3 most recent as a rollback window)
    if [ -n "$lt_id" ] && [ "$lt_id" != "None" ]; then
        echo "--- Cleaning up old launch template versions (keeping 3 most recent) ---"
        local old_lt_versions
        # awk prints all but the last 3 lines — avoids BSD head's lack of negative -n support on macOS
        old_lt_versions=$(aws ec2 describe-launch-template-versions \
            --launch-template-id "$lt_id" \
            --region "$AWS_REGION" \
            --query "LaunchTemplateVersions[*].VersionNumber" --output text \
            | tr '\t' '\n' | sort -n \
            | awk 'BEGIN{n=0} {lines[n++]=$0} END{for(i=0;i<n-3;i++) print lines[i]}')
        for v in $old_lt_versions; do
            aws ec2 delete-launch-template-versions \
                --launch-template-id "$lt_id" --versions "$v" --region "$AWS_REGION" > /dev/null 2>&1 || true
            echo "Deleted old launch template version $v"
        done
    fi

    echo ""
    echo "=== AMI Bake Complete ==="
    echo "  AMI ID:    $ami_id"
    echo "  AMI Name:  $ami_name"
    echo "  Next ASG launch will use this AMI for fast (~3-5 min) boots."
    echo ""
}

# Show the current pipeline AMIs and which one the launch template is using
step_ami_status() {
    echo "=== Pipeline AMI Status ==="

    # Make sure we are logged in to AWS before doing anything — stops silent failures early
    _ensure_aws_auth || return 1

    # List all pipeline AMIs sorted by creation date
    echo "--- Available AMIs ---"
    aws ec2 describe-images \
        --owners self \
        --filters "Name=name,Values=${AMI_NAME_PREFIX}-*" \
        --region "$AWS_REGION" \
        --query 'Images[*].[ImageId,Name,CreationDate,State]' \
        --output table 2>/dev/null || echo "No pipeline AMIs found."

    # Show which AMI the launch template currently uses
    echo ""
    echo "--- Current Launch Template AMI ---"
    aws ec2 describe-launch-template-versions \
        --filters "Name=tag:Project,Values=data-pipeline" \
        --versions '$Latest' \
        --region "$AWS_REGION" \
        --query 'LaunchTemplateVersions[0].LaunchTemplateData.ImageId' \
        --output text 2>/dev/null || echo "Could not determine current AMI."
    echo ""
}

# If called directly (not sourced), run the requested subcommand
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        bake)   step_bake_ami ;;
        status) step_ami_status ;;
        *)      echo "Usage: ami.sh bake|status"; exit 1 ;;
    esac
fi
