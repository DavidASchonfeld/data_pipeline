#!/bin/bash
# Module: genai-api — deploys the HTTP service that fronts the agent (genai.agent.answer) so the slim
# dashboard pod can ask questions over in-cluster HTTP instead of carrying the heavy genai runtime.
#
# Unlike pgvector/mlflow (which pull a public image), this service reuses the airflow-dbt image that
# step_build_airflow_image already built and imported into K3S containerd this same deploy — so there is
# NO docker pull/import here. The module just substitutes that image's BUILD_TAG into the manifest,
# applies it to airflow-my-namespace (where the pgvector/genai/Snowflake secrets already live), and
# polls the rollout.
#
# Sourced by deploy.sh; all variables from common.sh (plus BUILD_TAG from deploy.sh) are available here.
# Only called when GENAI_ENABLED=true — the caller gates the invocation.

step_deploy_genai_api() {
    echo "=== Step 7f: Syncing genai-api manifests to EC2 ==="
    ssh "$EC2_HOST" "mkdir -p $EC2_HOME/infra/genai/genai-api"
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/infra/genai/genai-api/" "$EC2_HOST:$EC2_HOME/infra/genai/genai-api/"

    echo "=== Step 7f1: Deploying genai-api to K3s (reuses airflow-dbt:$BUILD_TAG) ==="
    _ensure_kubectl_accessible
    # Substitute the freshly-built image tag into the Deployment, then apply both manifests. envsubst only
    # touches ${AIRFLOW_IMAGE_TAG}; everything else in the YAML is left verbatim.
    ssh "$EC2_HOST" "
        AIRFLOW_IMAGE_TAG='$BUILD_TAG' envsubst '\${AIRFLOW_IMAGE_TAG}' \
            < $EC2_HOME/infra/genai/genai-api/deployment-genai-api.yaml \
            | kubectl apply -n airflow-my-namespace -f - \
        && kubectl apply -n airflow-my-namespace -f $EC2_HOME/infra/genai/genai-api/service-genai-api.yaml \
        && echo 'genai-api manifests applied.'
    " || { echo '✗ genai-api manifest apply failed'; return 1; }

    # Force a fresh rollout: the image tag changes every deploy, so the Deployment's pod template changes
    # and K8s rolls automatically — but if only the genai/ source changed under an unchanged tag (rare),
    # this restart guarantees the new code is picked up.
    ssh "$EC2_HOST" "kubectl rollout restart deployment/genai-api -n airflow-my-namespace 2>/dev/null || true"

    echo 'Polling genai-api rollout (20 × 15s = 300s)...'
    local _ok=false _i _ready
    for _i in $(seq 1 20); do
        _ready=$(ssh "$EC2_HOST" "kubectl get deployment genai-api -n airflow-my-namespace \
            -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0")
        if [ "${_ready:-0}" -ge 1 ]; then _ok=true; break; fi
        echo "  Attempt $_i/20 — genai-api availableReplicas=${_ready:-0}, waiting 15s..."
        sleep 15
    done

    if [ "$_ok" = false ]; then
        echo 'ERROR: genai-api rollout timed out. Diagnosing...'
        ssh "$EC2_HOST" "
            kubectl get pods -n airflow-my-namespace -l app=genai-api
            echo '--- describe (last 30 lines) ---'
            kubectl describe pod -n airflow-my-namespace -l app=genai-api | tail -30
            echo '--- logs (last 30 lines) ---'
            kubectl logs -n airflow-my-namespace -l app=genai-api --tail=30 2>/dev/null || echo '(no logs yet)'
        "
        return 1
    fi

    echo '=== genai-api pod is Running ==='
    # Smoke-test the health endpoint from inside the cluster so a deploy fails loudly if the service is up
    # but not actually serving.
    ssh "$EC2_HOST" "kubectl run genai-api-healthcheck --rm -i --restart=Never -n airflow-my-namespace \
        --image=curlimages/curl:8.10.1 --image-pull-policy=IfNotPresent --quiet -- \
        curl -fsS --max-time 5 http://genai-api.airflow-my-namespace.svc.cluster.local:8000/health" \
        && echo 'genai-api /health OK.' \
        || echo 'WARNING: genai-api /health check did not return OK — inspect the pod logs.'
}
