#!/bin/bash
# Module: kafka — Kafka manifest sync, image pre-pull, StatefulSet deploy, and topic creation.
# Sourced by deploy.sh; all variables from common.sh are available here.

_cleanup_stale_kafka_pvc() {
    # Delete kafka-data-kafka-0 PVC/PV if it is affined to a different node than the current Ready node.
    # local-path provisioner pins PVs to the node where they were first created, so after an instance
    # replacement the old PVC blocks kafka-0 from scheduling on the new node.
    ssh "$EC2_HOST" "
        PVC_PHASE=\$(kubectl get pvc kafka-data-kafka-0 -n kafka \
            -o jsonpath='{.status.phase}' 2>/dev/null || echo '')
        [ -z \"\$PVC_PHASE\" ] && echo 'No Kafka PVC found — nothing to clean.' && exit 0
        PV_NAME=\$(kubectl get pvc kafka-data-kafka-0 -n kafka \
            -o jsonpath='{.spec.volumeName}' 2>/dev/null || echo '')
        if [ \"\$PVC_PHASE\" = 'Lost' ]; then
            # Lost PVC: delete both PVC and PV — leaving the Released PV causes new PVC to bind to it and
            # inherit its stale nodeAffinity, keeping kafka-0 Pending indefinitely.
            echo \"Kafka PVC is Lost — deleting PVC\${PV_NAME:+ and stale PV \$PV_NAME}...\"
            kubectl delete pvc kafka-data-kafka-0 -n kafka --ignore-not-found=true
            if [ -n \"\$PV_NAME\" ]; then
                kubectl patch pv \"\$PV_NAME\" -p '{\"metadata\":{\"finalizers\":null}}' 2>/dev/null || true
                kubectl delete pv \"\$PV_NAME\" --ignore-not-found=true
            fi
            sleep 2
            exit 0
        fi
        [ \"\$PVC_PHASE\" != 'Bound' ] && echo \"Kafka PVC phase is '\$PVC_PHASE' — skipping stale check.\" && exit 0
        [ -z \"\$PV_NAME\" ] && exit 0
        PV_NODE=\$(kubectl get pv \"\$PV_NAME\" \
            -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}' \
            2>/dev/null || echo '')
        [ -z \"\$PV_NODE\" ] && echo \"Kafka PV '\$PV_NAME' has no node affinity — not a local-path PV, skipping.\" && exit 0
        CURRENT_NODE=\$(kubectl get nodes --no-headers | awk '\$2 == \"Ready\" {print \$1}' | head -1)
        if [ -n \"\$CURRENT_NODE\" ] && [ \"\$PV_NODE\" != \"\$CURRENT_NODE\" ]; then
            echo \"Stale Kafka PVC: PV '\$PV_NAME' affined to '\$PV_NODE' but current node is '\$CURRENT_NODE'\"
            echo 'Deleting stale Kafka PVC + PV so local-path can provision fresh storage on the correct node...'
            kubectl delete pvc kafka-data-kafka-0 -n kafka --ignore-not-found=true
            kubectl patch pv \"\$PV_NAME\" -p '{\"metadata\":{\"finalizers\":null}}' 2>/dev/null || true
            kubectl delete pv \"\$PV_NAME\" --ignore-not-found=true
            sleep 2  # let K8s API record the deletion before StatefulSet apply
        else
            echo \"Kafka PVC OK — PV '\$PV_NAME' is on current node '\$CURRENT_NODE'\"
        fi
    "
}

step_deploy_kafka() {
    echo "=== Step 2b3: Syncing Kafka manifests to EC2 ==="
    # we use a plain Kubernetes manifest here instead of the old Bitnami Helm chart (simpler, no licensing issues)
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/kafka/k8s/" "$EC2_HOST:$EC2_HOME/kafka/k8s/"

    echo "=== Step 2b3a: Pre-pulling Kafka image into K3s containerd ==="
    # Pre-loads the Kafka image before the pod starts, so the rollout doesn't fail the 480s timeout waiting on a slow download.
    # Same approach used for MLflow and the Airflow image. crictl pull does nothing if the image is already there.
    # Retry loop: a concurrent AMI bake or heavy parallel load can reset the containerd socket mid-pull.

    # Clear orphaned containerd blobs and disk-pressure taint before pulling — the Kafka lchown overlayfs
    # error (2026-05-09) happens when the content store still holds stale layer bytes from prior failed
    # imports. Pulling on top of them re-enters the corrupt overlayfs state on every attempt.
    _ensure_disk_space
    _remove_disk_pressure_taint

    for _pull_attempt in 1 2 3; do
        if ssh "$EC2_HOST" "
            echo 'Pre-pulling Kafka image (attempt $_pull_attempt/3)...' &&
            sudo timeout 300 k3s crictl pull docker.io/apache/kafka:4.0.0 &&
            echo 'Kafka image ready in K3s containerd.'
        "; then
            break
        fi
        if [ "$_pull_attempt" -lt 3 ]; then
            echo "Kafka image pull attempt $_pull_attempt failed — checking containerd socket..."
            # Remove any partial/corrupt snapshot before retrying — lchown errors leave broken overlayfs state
            # that causes every subsequent crictl pull attempt to fail with the same "no such file" error.
            ssh "$EC2_HOST" "sudo k3s crictl rmi docker.io/apache/kafka:4.0.0 2>/dev/null || true
                sudo k3s ctr images rm docker.io/apache/kafka:4.0.0 2>/dev/null || true"
            # ctr images rm only drops the tag — corrupt layer bytes stay in the content store and cause
            # the next pull to hit the same lchown error. content gc actually reclaims them.
            ssh "$EC2_HOST" "sudo k3s ctr content gc 2>/dev/null || true"
            if ssh "$EC2_HOST" "sudo k3s ctr version >/dev/null 2>&1"; then
                echo "Containerd socket OK — retrying in 15s..."
                sleep 15
            else
                echo "Containerd socket unresponsive — waiting 30s..."
                sleep 30
            fi
        else
            echo "✗ Kafka image pre-pull failed after 3 attempts"
            return 1
        fi
    done

    # GC orphaned content blobs left behind by the Kafka pull — mirrors the post-import GC in airflow_image.sh:95.
    # Pulling a ~700 MB image adds enough churn to tip the node into disk-pressure if blobs from prior pulls linger.
    ssh "$EC2_HOST" "sudo k3s ctr content gc && echo 'Containerd GC complete after Kafka pull'" || true

    # Re-apply kubectl permissions — same reason as mlflow.sh: K3s can restart under load and reset k3s.yaml to 600
    _ensure_kubectl_accessible

    # Create kafka namespace before PVC check — _cleanup_stale_kafka_pvc needs the namespace to exist
    ssh "$EC2_HOST" "kubectl create namespace kafka --dry-run=client -o yaml | kubectl apply -f -"

    # Clean up stale Kafka PVC before applying the StatefulSet — if PVC is affined to the old node,
    # kafka-0 can't schedule anywhere and will time out. Same pattern as PostgreSQL PVC cleanup.
    _cleanup_stale_kafka_pvc

    echo "=== Step 2b4: Deploying Kafka to K3s (safe to run multiple times) ==="
    # kubectl apply creates Kafka if it doesn't exist, or updates it if it does — safe to run every time.
    # Kafka lives in its own 'kafka' namespace, separate from airflow-my-namespace.
    ssh "$EC2_HOST" "
        # Apply StatefulSet + Services from the plain manifest
        kubectl apply -f $EC2_HOME/kafka/k8s/kafka.yaml \
        && echo 'Kafka manifests applied.'

        # Deadlock guard: Kubernetes won't replace a pod that's already Not-Ready, even after you apply a config change.
        # kubectl apply updates Kubernetes's internal database (etcd) but the running pod doesn't change until it's restarted.
        # We detect this stuck state (the desired version differs from the running version, and the pod is Not-Ready)
        # and delete the pod so Kubernetes can start a fresh one with the correct config.
        CURRENT_REV=\$(kubectl get statefulset kafka -n kafka \
            -o jsonpath='{.status.currentRevision}' 2>/dev/null || echo '')
        UPDATE_REV=\$(kubectl get statefulset kafka -n kafka \
            -o jsonpath='{.status.updateRevision}' 2>/dev/null || echo '')
        POD_READY=\$(kubectl get pod kafka-0 -n kafka \
            -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null || echo '')

        if [ -n \"\$CURRENT_REV\" ] && [ -n \"\$UPDATE_REV\" ] \
            && [ \"\$CURRENT_REV\" != \"\$UPDATE_REV\" ] && [ \"\$POD_READY\" = False ]; then
            echo \"DEADLOCK DETECTED: pending update (\$CURRENT_REV -> \$UPDATE_REV), kafka-0 Not Ready.\"
            echo 'Gracefully deleting kafka-0 to let controller apply new spec...'
            # 30-second grace period gives Kafka time to flush its data to disk, avoiding a slow recovery scan on the next startup
            kubectl delete pod kafka-0 -n kafka --grace-period=30
            # Wait for the old pod to fully stop before we start watching for the new one — otherwise we might watch the wrong pod
            kubectl wait pod/kafka-0 -n kafka --for=delete --timeout=60s \
                || echo 'Note: kafka-0 took > 60s to terminate — continuing anyway.'
        else
            echo \"No deadlock (currentRevision=\$CURRENT_REV, updateRevision=\$UPDATE_REV, podReady=\$POD_READY).\"
        fi

        # Wait for the rollout to fully complete.
        # We use rollout status (not kubectl wait) because kubectl wait can mistakenly return success for the OLD pod
        # right before it's deleted. rollout status specifically waits for the NEW pod to be ready.
        # 480s timeout: Kafka's startup health check can take up to 290s (20s initial delay + 18 retries × 15s),
        # plus extra buffer for scheduling and first readiness.
        echo 'Waiting for Kafka rollout to complete (readiness probe gates on port 9092, up to 480s)...'
        # Run rollout in background; print pod phase every 30s so the terminal isn't silent for 8 minutes
        kubectl rollout status statefulset/kafka -n kafka --timeout=480s &
        _KAFKA_ROLLOUT_PID=\$!
        while kill -0 \"\$_KAFKA_ROLLOUT_PID\" 2>/dev/null; do
            sleep 30
            kill -0 \"\$_KAFKA_ROLLOUT_PID\" 2>/dev/null || break
            echo \"  [\$(date '+%H:%M:%S')] kafka-0: \$(kubectl get pod kafka-0 -n kafka --no-headers 2>/dev/null | awk '{print \"status=\" \$3 \" ready=\" \$2}' || echo 'not found')\"
        done
        wait \"\$_KAFKA_ROLLOUT_PID\" || {
            echo 'WARNING: Kafka rollout did not complete — skipping topic creation. Run deploy again once it is running.'
            # Look at the exit code to understand why it failed:
            # exit 137 means Kubernetes killed it for using too much memory (OOMKill)
            # exit 1 means Kafka started too slowly and the health check timed out
            echo '--- kafka-0 pod conditions and last state ---'
            kubectl describe pod kafka-0 -n kafka \
                | grep -E 'Last State|Exit Code|OOMKilled|Conditions|Ready|Started|Finished|Reason'
            echo '--- kafka-0 current logs (last 30 lines) ---'
            kubectl logs kafka-0 -n kafka --tail=30 2>/dev/null \
                || kubectl logs kafka-0 -n kafka --previous --tail=30 2>/dev/null \
                || echo '(no logs available — pod may not have started)'
            exit 0
        }

        # Create topics — --if-not-exists means Kafka skips creation if the topic is already there — safe to run every time
        # the kafka-topics.sh script is at /opt/kafka/bin/ in this image — it's not on the PATH the way it was in the old Bitnami image
        # Retry loop: K3S containerd can briefly report "container not found" right after a pod turns Ready
        # when the server is under heavy load — waiting and retrying recovers without any config changes
        for _t in 1 2 3; do
            kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
                --bootstrap-server localhost:9092 --create --if-not-exists \
                --topic stocks-financials-raw --partitions 1 --replication-factor 1 \
            && echo 'Topic stocks-financials-raw ready.' && break
            [ \"\$_t\" -lt 3 ] && echo \"Topic create attempt \$_t failed, retrying in 10s...\" && sleep 10
        done

        for _t in 1 2 3; do
            kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
                --bootstrap-server localhost:9092 --create --if-not-exists \
                --topic weather-hourly-raw --partitions 1 --replication-factor 1 \
            && echo 'Topic weather-hourly-raw ready.' && break
            [ \"\$_t\" -lt 3 ] && echo \"Topic create attempt \$_t failed, retrying in 10s...\" && sleep 10
        done

        echo 'Kafka topics:'
        kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
            --list --bootstrap-server localhost:9092
    "
}
