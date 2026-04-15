#!/bin/bash
# Boot script for pipeline instances launched from a pre-baked AMI.
# Runs automatically on every ASG launch — starts services and refreshes credentials
# so the dashboard is ready for visitors within a few minutes.
set -euo pipefail
exec > /var/log/pipeline-boot.log 2>&1

echo "=== Pipeline boot started at $(date -u) ==="

# --- Start K3s (lightweight Kubernetes) so pods can run ---
echo "Starting K3s..."
sudo systemctl start k3s

# Wait for the K3s node to be ready before doing anything else
for i in $(seq 1 30); do
    if kubectl get nodes 2>/dev/null | grep -q ' Ready'; then
        echo "K3s node is Ready (attempt $i)"
        break
    fi
    echo "Waiting for K3s to be ready (attempt $i/30)..."
    sleep 10
done

# --- Start Docker (needed for image management commands) ---
sudo systemctl start docker

# --- Fix permissions so kubectl works without sudo ---
sudo chmod 644 /etc/rancher/k3s/k3s.yaml

# --- Refresh ECR login so K3s can pull the latest dashboard image ---
echo "Refreshing ECR pull secrets..."
ECR_PASSWORD=$(aws ecr get-login-password --region ${aws_region})
for NS in default airflow-my-namespace; do
    kubectl create secret docker-registry ecr-credentials \
        -n "$NS" \
        --docker-server=${ecr_registry} \
        --docker-username=AWS \
        --docker-password="$ECR_PASSWORD" \
        --dry-run=client -o yaml | kubectl apply -n "$NS" -f -
done

# --- Apply the Flask pod manifest (AMI bake clears K3s state, so pod must be re-created on boot) ---
echo "Applying dashboard pod manifest..."
# sed substitutes the ECR_REGISTRY placeholder in the pod manifest;
# $${ECR_REGISTRY} in this .tpl file renders to the literal string that sed matches against
sed 's|$${ECR_REGISTRY}|${ecr_registry}|g' \
    /home/ubuntu/dashboard/manifests/pod-flask.yaml | kubectl apply -n default -f -

# --- Wait for the dashboard pod to be ready so visitors can see the website ---
echo "Waiting for dashboard pod to be ready..."
kubectl wait pod/my-kuber-pod-flask -n default \
    --for=condition=Ready --timeout=300s 2>/dev/null || \
    echo "WARNING: Flask pod did not become Ready within 300s"

echo "=== Pipeline boot completed at $(date -u) ==="
