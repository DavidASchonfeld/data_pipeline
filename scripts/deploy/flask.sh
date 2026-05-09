#!/bin/bash
# Module: flask — Dashboard rsync, ECR credential setup, Flask Docker build/push, pod lifecycle, readiness check.
# Sourced by deploy.sh; all variables from common.sh are available here.

step_deploy_flask() {
    echo "=== Step 3: Syncing dashboard build files to EC2 ==="
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/dashboard/" "$EC2_HOST:$EC2_BUILD_PATH/"

    echo "=== Step 4a: Configuring ECR credential helper on EC2 ==="
    # amazon-ecr-credential-helper is the recommended way to authenticate with ECR. It automatically gets
    # short-lived access tokens using the EC2 machine's IAM role, so Docker never has to store AWS credentials
    # on disk (which avoids the "unencrypted credentials" warning you'd see with `docker login`).
    ssh "$EC2_HOST" "
        # Install ECR credential helper if not already present
        if ! command -v docker-credential-ecr-login &>/dev/null; then
            sudo apt-get install -y -q amazon-ecr-credential-helper
        fi
        # Install docker buildx if it's not already there. The version in Ubuntu's default apt repo doesn't
        # include it, so we download the binary directly from GitHub.
        if ! docker buildx version &>/dev/null; then
            BUILDX_VER=\$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
                | python3 -c \"import sys,json; print(json.load(sys.stdin)['tag_name'])\")
            mkdir -p ~/.docker/cli-plugins
            curl -fsSL \"https://github.com/docker/buildx/releases/download/\${BUILDX_VER}/buildx-\${BUILDX_VER}.linux-arm64\" \
                -o ~/.docker/cli-plugins/docker-buildx
            chmod +x ~/.docker/cli-plugins/docker-buildx
            echo \"Installed buildx \${BUILDX_VER}\"
        fi
        # Tell Docker to use the ECR credential helper for this registry — updates ~/.docker/config.json, and is safe to run multiple times
        python3 -c \"
import json, pathlib
p = pathlib.Path.home() / '.docker/config.json'
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault('credHelpers', {})['$ECR_REGISTRY'] = 'ecr-login'
p.parent.mkdir(exist_ok=True)
p.write_text(json.dumps(cfg, indent=2))
print('ECR credential helper configured')
        \"
    "

    echo "=== Step 4: Building Docker image on EC2 and pushing to ECR ==="
    # Tag the currently running image as 'previous' before overwriting — enables rollback (see procedure in deploy.sh)
    ssh "$EC2_HOST" "docker tag $FLASK_IMAGE my-flask-app:previous 2>/dev/null || true"
    # WHY we push to ECR instead of keeping the image local:
    #   K3S now uses its default containerd runtime (not the legacy --docker mode).
    #   K3S and Docker keep images in separate stores, so a `docker build` image is NOT visible to K3S.
    #   Instead of manually importing it, we push to ECR (AWS's private image registry) and let K3S pull
    #   from there — the standard Kubernetes pattern on AWS.
    #
    #   Authentication is handled by the ECR credential helper from Step 4a, which uses the EC2 machine's
    #   IAM role automatically — no `docker login` needed, no credentials stored on disk.
    # DOCKER_BUILDKIT=1 turns on BuildKit, Docker's modern build engine (the old builder is deprecated)
    ssh "$EC2_HOST" "cd $EC2_BUILD_PATH \
        && DOCKER_BUILDKIT=1 docker build -t $FLASK_IMAGE . \
        && docker tag $FLASK_IMAGE $ECR_IMAGE \
        && docker push $ECR_IMAGE"

    echo "=== Step 5: Refreshing ECR pull secret in Kubernetes ==="
    # WHY this step is needed:
    #   K3S needs credentials to pull images from ECR, which is a private registry.
    #   We store those credentials as a Kubernetes "docker-registry" secret named "ecr-credentials".
    #   Both the Flask pod and Airflow pods reference this secret via `imagePullSecrets`.
    #
    #   ECR tokens expire after 12 hours, so we refresh the secret on every deploy to keep it valid.
    #   `--dry-run=client -o yaml | kubectl apply` creates the secret if it's new, or updates it if it
    #   already exists — without throwing an error either way.
    #
    # WHY apply to both namespaces:
    #   Kubernetes only looks for pull secrets in the same namespace as the pod.
    #   A secret in the wrong namespace is silently ignored — the pod then tries to pull without
    #   credentials and fails with ImagePullBackOff. We apply to both:
    #   - default namespace (Flask pod)
    #   - airflow-my-namespace (Airflow pods: scheduler, webserver, dag-processor, triggerer)

    # Create the secret in default namespace (Flask)
    ssh "$EC2_HOST" "kubectl create secret docker-registry ecr-credentials \
        -n default \
        --docker-server=$ECR_REGISTRY \
        --docker-username=AWS \
        --docker-password=\$(aws ecr get-login-password --region $AWS_REGION) \
        --dry-run=client -o yaml | kubectl apply -n default -f -"

    # Create the secret in airflow-my-namespace (Airflow pods)
    ssh "$EC2_HOST" "kubectl create secret docker-registry ecr-credentials \
        -n airflow-my-namespace \
        --docker-server=$ECR_REGISTRY \
        --docker-username=AWS \
        --docker-password=\$(aws ecr get-login-password --region $AWS_REGION) \
        --dry-run=client -o yaml | kubectl apply -n airflow-my-namespace -f -"

    echo "=== Step 6: Restarting Flask pod to pick up the new image ==="
    # WHY delete+recreate instead of just "restart":
    #
    #   Kubernetes has two common ways to run containers:
    #
    #   1. Plain Pod (what we have — see dashboard/manifests/pod-flask.yaml, line: "kind: Pod")
    #      A single container with no supervisor watching over it. If it crashes, it stays dead.
    #      There's no built-in "restart" command — you have to delete the pod and re-apply the manifest.
    #
    #   2. Deployment (best practice for production)
    #      A controller that manages the pod's lifecycle. Supports `kubectl rollout restart`, which
    #      starts the new pod first, waits for it to be healthy, then kills the old one (zero downtime).
    #      Also auto-restarts the pod if it crashes.
    #
    #   How do you know which one this is? Open dashboard/manifests/pod-flask.yaml — "kind: Pod" means
    #   it's a plain Pod. "kind: Deployment" would mean a Deployment. For a personal project a plain
    #   Pod is fine; for production use a Deployment.
    #
    # "--ignore-not-found" prevents an error if the pod doesn't exist yet (e.g. first deploy)
    # "-n default" is required: the kubectl context default namespace is airflow-my-namespace on this cluster
    #
    # WHY kubectl wait --for=delete before kubectl apply:
    #   "kubectl delete" kicks off graceful termination but returns immediately — the pod is still visible
    #   in Kubernetes while it shuts down ("Terminating" status). If we run kubectl apply at that moment,
    #   Kubernetes sees the pod object still exists and prints "unchanged" without creating a new one.
    #   We wait for the old pod to fully disappear before applying. The "|| true" lets the script
    #   continue if the pod was already gone (first deploy or already fully deleted).
    ssh "$EC2_HOST" "kubectl delete pod $FLASK_POD -n default --ignore-not-found=true && kubectl wait --for=delete pod/$FLASK_POD -n default --timeout=30s 2>/dev/null || true"

    # pod-flask.yaml in git contains ${ECR_REGISTRY} as a placeholder for the ECR image URI.
    # We substitute the real value from .env.deploy before applying, so the AWS account ID
    # stays out of version control. envsubst replaces ${ECR_REGISTRY} with the actual URI.
    # service-flask.yaml has no secrets, so it's applied as-is.
    # envsubst lives at /opt/homebrew/bin on Apple Silicon Macs; we fall back to sed if it's not available
    if command -v envsubst &>/dev/null; then
        ECR_REGISTRY="$ECR_REGISTRY" envsubst '${ECR_REGISTRY}' < "$PROJECT_ROOT/dashboard/manifests/pod-flask.yaml" > /tmp/pod-flask-rendered.yaml
    else
        sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" "$PROJECT_ROOT/dashboard/manifests/pod-flask.yaml" > /tmp/pod-flask-rendered.yaml
    fi
    rsync $RSYNC_FLAGS /tmp/pod-flask-rendered.yaml "$EC2_HOST:/tmp/pod-flask.yaml"
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/dashboard/manifests/service-flask.yaml" "$EC2_HOST:/tmp/"
    # Clear the cached Flask image from K3s containerd so it pulls the freshly-pushed version from ECR.
    # Required because imagePullPolicy: IfNotPresent skips ECR pulls when any cached image exists.
    ssh "$EC2_HOST" "sudo k3s crictl rmi $ECR_IMAGE 2>/dev/null || true"
    ssh "$EC2_HOST" "kubectl apply -f /tmp/service-flask.yaml && kubectl apply -f /tmp/pod-flask.yaml"
}

step_verify_flask() {
    echo "=== Step 8: Verifying deployment ==="

    # Clear any residual disk-pressure taint before Flask tries to schedule — the airflow rollout
    # above frees image cache but the taint may still be set from an earlier high-disk moment.
    _ensure_disk_space

    # Inner function: wait for pod Ready and return status code (0=ready, 1=not ready)
    # 180s timeout matches the pod's livenessProbe.initialDelaySeconds=120 + prewarm buffer.
    _wait_flask_ready() {
        ssh "$EC2_HOST" "
            echo 'Waiting for $FLASK_POD to be ready (up to 180s)...' &&
            kubectl wait pod/$FLASK_POD -n default --for=condition=Ready --timeout=180s &&
            echo '' &&
            echo 'Pod is Running. All pods:' &&
            kubectl get pods -n default
        "
    }

    if ! _wait_flask_ready; then
        # Check if the pod was evicted due to disk pressure — if so, retry once after cleanup
        _POD_PHASE=$(ssh "$EC2_HOST" "kubectl get pod $FLASK_POD -n default -o jsonpath='{.status.phase}' 2>/dev/null || echo 'Unknown'")
        if [ "$_POD_PHASE" = "Failed" ]; then
            _EVICT_REASON=$(ssh "$EC2_HOST" "kubectl get pod $FLASK_POD -n default -o jsonpath='{.status.reason}' 2>/dev/null || echo ''")
        fi
        if [ "${_EVICT_REASON:-}" = "Evicted" ]; then
            echo ""
            echo "Flask pod was evicted (likely DiskPressure) — running cleanup and retrying once..."
            _ensure_disk_space
            # Delete the evicted pod shell and re-apply so K3s schedules a fresh one
            ssh "$EC2_HOST" "kubectl delete pod $FLASK_POD -n default --ignore-not-found=true && sleep 5"
            ssh "$EC2_HOST" "kubectl apply -f /tmp/pod-flask.yaml"
            if ! _wait_flask_ready; then
                echo ""
                echo "WARNING: Flask pod did not become Ready after retry. Current state:"
                ssh "$EC2_HOST" "kubectl get pods -n default && echo '' && kubectl describe pod $FLASK_POD -n default | tail -20"
            fi
        else
            echo ""
            echo "WARNING: Flask pod did not become Ready within 180s. Current state:"
            ssh "$EC2_HOST" "kubectl get pods -n default && echo '' && kubectl describe pod $FLASK_POD -n default | tail -20"
        fi
    fi

    # Verify public connectivity — catches cases where the pod is healthy but the security group blocks port 32147
    _DASHBOARD_IP=$(ssh -G "$EC2_HOST" 2>/dev/null | awk '/^hostname / {print $2}')
    if [ -n "$_DASHBOARD_IP" ]; then
        if curl -fsSL -o /dev/null --connect-timeout 5 "http://$_DASHBOARD_IP:32147/health" 2>/dev/null; then
            echo "✓ Dashboard publicly accessible at http://$_DASHBOARD_IP:32147/dashboard/"
        else
            echo ""
            echo "WARNING: Flask pod is Ready but not publicly reachable at http://$_DASHBOARD_IP:32147/"
            echo "  The AWS Security Group may not allow inbound traffic on port 32147."
            echo "  Fix: ./scripts/deploy.sh --provision   (runs terraform apply to update the security group)"
        fi
    fi
}
