#!/bin/bash
# Module: airflow_pods — Helm upgrade, Airflow pod restarts (parallel waits), and ml-venv setup.
# Sourced by deploy.sh; BUILD_TAG must be set in deploy.sh before calling step_helm_upgrade.

_wait_scheduler_exec() {
    # Poll until kubectl exec can actually reach the scheduler container — pod Ready condition is not enough.
    # The K3S container runtime needs a few extra seconds after the pod turns Ready before exec connections succeed.
    # Called before every kubectl exec into the scheduler so each step gets its own readiness confirmation.
    ssh "$EC2_HOST" "
        for i in \$(seq 1 30); do
            if kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /bin/true 2>/dev/null; then
                echo \"Scheduler container exec-ready (attempt \$i)\"
                break
            fi
            if [ \$i -eq 30 ]; then
                echo 'ERROR: Scheduler container did not become exec-ready after 60s'
                exit 1
            fi
            sleep 2
        done
    "
}

_cleanup_stale_nodes() {
    # Remove NotReady nodes left over from a prior EC2 instance — prevents "untolerated taint" scheduling errors.
    # AMI-baked K3s etcd retains the old node entry; the new instance registers as a second node until the old one is removed.
    ssh "$EC2_HOST" "
        STALE=\$(kubectl get nodes --no-headers 2>/dev/null | awk '\$2 == \"NotReady\" {print \$1}')
        if [ -z \"\$STALE\" ]; then echo 'No stale NotReady nodes found.'; exit 0; fi
        for NODE in \$STALE; do
            echo \"Removing stale NotReady node: \$NODE\"
            kubectl delete node \"\$NODE\" --ignore-not-found=true
        done
    "
}

_cleanup_stale_pg_pvc() {
    # Delete PostgreSQL PVC/PV whose node affinity points to a different node — prevents pod Pending after instance replacement.
    # local-path provisioner locks PVs to the hostname where they were created; a new AMI instance has a different hostname.
    ssh "$EC2_HOST" "
        PVC_PHASE=\$(kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace \
            -o jsonpath='{.status.phase}' 2>/dev/null || echo '')
        [ \"\$PVC_PHASE\" != 'Bound' ] && exit 0  # no Bound PVC — nothing to check
        PV_NAME=\$(kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace \
            -o jsonpath='{.spec.volumeName}' 2>/dev/null || echo '')
        [ -z \"\$PV_NAME\" ] && exit 0
        # local-path provisioner records the creating node's hostname in nodeAffinity
        PV_NODE=\$(kubectl get pv \"\$PV_NAME\" \
            -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}' \
            2>/dev/null || echo '')
        [ -z \"\$PV_NODE\" ] && exit 0  # no node affinity — not a local-path PV, skip
        CURRENT_NODE=\$(kubectl get nodes --no-headers | awk '\$2 == \"Ready\" {print \$1}' | head -1)
        if [ -n \"\$CURRENT_NODE\" ] && [ \"\$PV_NODE\" != \"\$CURRENT_NODE\" ]; then
            echo \"Stale PostgreSQL PVC: PV '\$PV_NAME' is affined to '\$PV_NODE' but current node is '\$CURRENT_NODE'\"
            echo 'Deleting stale PVC + PV so Helm can provision fresh storage on the correct node...'
            kubectl delete pvc data-airflow-postgresql-0 -n airflow-my-namespace --ignore-not-found=true
            kubectl delete pv \"\$PV_NAME\" --ignore-not-found=true
            sleep 2  # give K8s API a moment to record the deletion before Helm runs
        else
            echo \"PostgreSQL PVC OK — PV '\$PV_NAME' is on current node '\$CURRENT_NODE'\"
        fi
    "
}

_cleanup_stale_dag_log_pvcs() {
    # Delete dag-pvc/dag-pv and log-pvc/log-pv if their bound PVs have nodeAffinity pointing to the wrong node.
    # K3s etcd baked into the AMI retains PV objects from the baking instance; a replacement instance has a
    # different hostname, so the provisioner-injected nodeAffinity no longer matches and pods sit Pending.
    # This mirrors the same pattern used by _cleanup_stale_pg_pvc and _cleanup_stale_kafka_pvc.
    local MANIFESTS_PATH="$EC2_HOME/airflow/manifests"
    for ENTRY in \
        "dag-pvc dag-pv pv-dags.yaml pvc-dags.yaml airflow-my-namespace" \
        "log-pvc log-pv pv-airflow-logs.yaml pvc-airflow-logs.yaml airflow-my-namespace"; do
        # Split the whitespace-separated entry into named variables
        read -r PVC_NAME PV_MANIFEST_NAME PV_FILE PVC_FILE NS <<< "$ENTRY"
        ssh "$EC2_HOST" "
            PVC_PHASE=\$(kubectl get pvc '$PVC_NAME' -n '$NS' \
                -o jsonpath='{.status.phase}' 2>/dev/null || echo '')
            if [ -z \"\$PVC_PHASE\" ]; then
                echo 'No $PVC_NAME found — nothing to check.'
                exit 0
            fi
            if [ \"\$PVC_PHASE\" != 'Bound' ]; then
                echo \"$PVC_NAME phase is '\$PVC_PHASE' — skipping stale check.\"
                exit 0
            fi
            # Get the PV that is actually bound (may be a generated pvc-abc123 name, not dag-pv)
            BOUND_PV=\$(kubectl get pvc '$PVC_NAME' -n '$NS' \
                -o jsonpath='{.spec.volumeName}' 2>/dev/null || echo '')
            [ -z \"\$BOUND_PV\" ] && exit 0
            # local-path provisioner records the creating node's hostname in nodeAffinity
            PV_NODE=\$(kubectl get pv \"\$BOUND_PV\" \
                -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}' \
                2>/dev/null || echo '')
            [ -z \"\$PV_NODE\" ] && echo \"$PVC_NAME PV '\$BOUND_PV' has no node affinity — not stale, skipping.\" && exit 0
            CURRENT_NODE=\$(kubectl get nodes --no-headers | awk '\$2 == \"Ready\" {print \$1}' | head -1)
            if [ -n \"\$CURRENT_NODE\" ] && [ \"\$PV_NODE\" != \"\$CURRENT_NODE\" ]; then
                echo \"Stale $PVC_NAME: PV '\$BOUND_PV' affined to '\$PV_NODE' but current node is '\$CURRENT_NODE'\"
                echo 'Deleting stale PVC + bound PV so fresh manifests can provision correct storage...'
                kubectl delete pvc '$PVC_NAME' -n '$NS' --ignore-not-found=true
                kubectl delete pv \"\$BOUND_PV\" --ignore-not-found=true
                # Also remove the named manifest PV if it exists separately (may be stuck in Available state)
                kubectl delete pv '$PV_MANIFEST_NAME' --ignore-not-found=true
                sleep 2  # give K8s API a moment to record the deletion before re-applying
                kubectl apply -f $MANIFESTS_PATH/$PV_FILE
                kubectl apply -f $MANIFESTS_PATH/$PVC_FILE -n '$NS'
                echo \"Recreated $PVC_NAME with fresh PV (no stale nodeAffinity)\"
            else
                echo \"$PVC_NAME OK — PV '\$BOUND_PV' is on current node '\$CURRENT_NODE'\"
            fi
        "
    done
}

step_helm_upgrade() {
    echo "=== Step 2d: Applying Helm values to live Airflow release ==="
    # Copying values.yaml to EC2 (step 2b) just puts the file there — it does NOT update the live Airflow deployment.
    # helm upgrade is what actually applies those changes (memory limits, worker count, probes) to the running pods.
    # Without this step, any values.yaml changes you make would be ignored until someone runs helm upgrade manually.
    #
    # --version 1.20.0: locks the Helm chart to the Airflow 3.x version (we upgraded from 1.15.0 on 2026-04-06)
    # No --reuse-values: we pass only values.yaml — using --reuse-values would pull in old 2.x Helm settings that break the 3.x schema
    # migrateDatabaseJob.useHelmHooks: false in values.yaml means helm upgrade returns right away —
    #   the database migration runs in the background, and pods wait for it using init containers
    # Note: each flag is on its own line with no inline comments — inside a double-quoted SSH string, bash does NOT
    #   strip # comments. They become literal text passed to helm, which breaks the command. --force would end up
    #   on its own line and be interpreted as a separate command ("command not found").
    # --set overrides the image tag in values.yaml with the fresh BUILD_TAG from this deploy, so K3S loads the new image
    # --install: installs the release if it doesn't exist yet (e.g. on a fresh server), upgrades if it does
    # Delete one-time jobs before upgrade — useHelmHooks:false creates them without Helm ownership labels,
    # so helm upgrade refuses to adopt them on subsequent runs. Safe: both jobs complete during first install.
    ssh "$EC2_HOST" "kubectl delete job airflow-create-user airflow-run-airflow-migrations \
        -n airflow-my-namespace --ignore-not-found=true"

    _cleanup_stale_nodes          # remove NotReady nodes left over from prior EC2 instance
    _cleanup_stale_pg_pvc         # remove PostgreSQL PVC/PV bound to wrong node before Helm provisions storage
    _cleanup_stale_dag_log_pvcs   # remove dag-pvc/log-pvc PVs with stale nodeAffinity from AMI bake

    # Ensure the log hostPath is group-writable by GID 0 (airflow's primary group) before Helm applies fsGroup=0;
    # without this the dag-processor crashes on first start because it can't create /opt/airflow/logs/dag_processor/
    ssh "$EC2_HOST" "sudo chown ubuntu:root /opt/airflow/logs 2>/dev/null; sudo chmod 775 /opt/airflow/logs"

    ssh "$EC2_HOST" "helm upgrade --install airflow apache-airflow/airflow \
        -n airflow-my-namespace \
        --version 1.20.0 \
        --timeout 10m \
        --force \
        --set images.airflow.tag=$BUILD_TAG \
        -f $EC2_HELM_PATH/values.yaml"

    # Double-check that helm actually updated the image tag — force-update the StatefulSet directly if it didn't (helm can silently skip updates in some cases)
    ssh "$EC2_HOST" "
        ACTUAL_TAG=\$(kubectl get statefulset airflow-scheduler -n airflow-my-namespace \
            -o jsonpath='{.spec.template.spec.containers[?(@.name==\"scheduler\")].image}' 2>/dev/null || echo '')
        echo \"StatefulSet scheduler image after helm upgrade: \$ACTUAL_TAG\"
        if [ \"\$ACTUAL_TAG\" != 'airflow-dbt:$BUILD_TAG' ]; then
            echo 'WARNING: Helm did not update scheduler image — force-patching StatefulSet...'
            kubectl set image statefulset/airflow-scheduler \
                scheduler=airflow-dbt:$BUILD_TAG \
                -n airflow-my-namespace
        else
            echo 'OK: StatefulSet has the correct image tag.'
        fi
    "

    echo "=== Step 2e: Applying Airflow service manifest ==="
    # Re-apply the Airflow UI service so its pod selector stays in sync with any changes in values.yaml
    # (for example, component label renames between Airflow 2.x and 3.x).
    # Without this step, helm upgrade doesn't update our manually-created NodePort service, so any label changes would be silently ignored.
    ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"

    echo "=== Step 2f: Waiting for Airflow database migrations to complete ==="
    # helm upgrade with useHelmHooks:false returns immediately — the migration job runs in the background.
    # On a fresh spot instance, PostgreSQL must initialise its data directory from scratch before the
    # migration job can connect; the full chain (postgres init → job connect → all schema revisions)
    # takes several minutes.  Waiting here means step 7 never restarts pods while migrations are still
    # in progress, which eliminates the wait-for-airflow-migrations init container restart loop
    # (see 2026-04-13 incident).

    # Phase 1: wait for PostgreSQL before checking the migration job.
    # helm upgrade creates both PostgreSQL and the migration job at the same time. On a fresh spot
    # instance, PostgreSQL needs 2-5 min to create its data directory and start accepting connections.
    # The migration job starts immediately and retries with exponential backoff (10s, 20s, 40s...);
    # if PostgreSQL takes longer than the cumulative backoff window, the job exhausts its retry limit
    # (backoffLimit=6) and enters a permanent Failed state. Waiting for PostgreSQL first ensures the
    # database is accepting connections, so the migration job's next retry (or a recreated job) succeeds.
    ssh "$EC2_HOST" "
        # Log state at the start so the deploy log shows what PostgreSQL was doing before we waited
        echo 'PostgreSQL pod current status:'
        kubectl get pod airflow-postgresql-0 -n airflow-my-namespace --no-headers 2>/dev/null || true

        # Early-exit: a pod with DisruptionTarget=True or in Terminating phase can never become Ready —
        # the node controller evicted it (e.g. after a NodeNotReady event). Waiting 600s would be wasted;
        # force-delete now so the StatefulSet controller can start a fresh pod immediately.
        DISRUPTION=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
            -o jsonpath='{.status.conditions[?(@.type==\"DisruptionTarget\")].status}' 2>/dev/null || echo '')
        POD_PHASE=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
            --no-headers 2>/dev/null | awk '{print \$3}' || echo '')
        if [ \"\$DISRUPTION\" = 'True' ] || echo \"\$POD_PHASE\" | grep -qi 'Terminating'; then
            echo 'PostgreSQL pod is disrupted/terminating — force-deleting so Kubernetes can schedule a fresh pod immediately...'
            kubectl delete pod airflow-postgresql-0 -n airflow-my-namespace \
                --force --grace-period=0 2>/dev/null || true
            sleep 3  # give the API server a moment to record the deletion before polling begins
        fi

        # Detect a stale PVC so the pod does not wait 600s before we notice it cannot mount storage
        PVC_PHASE=\$(kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace \
            -o jsonpath='{.status.phase}' 2>/dev/null || echo '')
        echo \"PostgreSQL PVC phase: \$PVC_PHASE\"
        if [ \"\$PVC_PHASE\" = 'Lost' ]; then
            # PVC is Lost — backing PV path is gone; delete so K3s provisions fresh storage on next pod start
            echo 'PVC is in Lost state — deleting so K3s can provision fresh storage on the new pod...'
            kubectl delete pvc data-airflow-postgresql-0 -n airflow-my-namespace --ignore-not-found=true
            sleep 3  # let K3s reconcile before polling starts
        elif [ \"\$PVC_PHASE\" = 'Bound' ]; then
            # Bound PVC can still be stale if bound to a different node or if its backing directory is gone
            PV_NAME=\$(kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace \
                -o jsonpath='{.spec.volumeName}' 2>/dev/null || echo '')
            if [ -n \"\$PV_NAME\" ]; then
                # Node-affinity check — local-path locks PVs to the node they were created on
                PV_NODE=\$(kubectl get pv \"\$PV_NAME\" \
                    -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}' \
                    2>/dev/null || echo '')
                READY_NODE=\$(kubectl get nodes --no-headers | awk '\$2 == \"Ready\" {print \$1}' | head -1)
                # Path check — local-path uses spec.hostPath.path (not spec.local.path which was wrong before)
                PV_PATH=\$(kubectl get pv \"\$PV_NAME\" \
                    -o jsonpath='{.spec.hostPath.path}' 2>/dev/null || echo '')
                STALE=false
                if [ -n \"\$PV_NODE\" ] && [ -n \"\$READY_NODE\" ] && [ \"\$PV_NODE\" != \"\$READY_NODE\" ]; then
                    # PV is affined to a different node — stale from an AMI-based instance replacement
                    echo \"PV \$PV_NAME is affined to '\$PV_NODE' but current node is '\$READY_NODE' — stale PVC\"
                    STALE=true
                elif [ -n \"\$PV_PATH\" ] && ! sudo test -d \"\$PV_PATH\"; then
                    # PV directory missing on this node — stale from a prior instance or spot rebuild
                    echo \"PV \$PV_NAME points to missing path \$PV_PATH — stale PVC\"
                    STALE=true
                fi
                if [ \"\$STALE\" = true ]; then
                    echo 'Deleting stale PVC + PV to force fresh provisioning on the correct node...'
                    kubectl delete pvc data-airflow-postgresql-0 -n airflow-my-namespace --ignore-not-found=true
                    kubectl delete pv \"\$PV_NAME\" --ignore-not-found=true
                    sleep 3  # let K3s reconcile before polling starts
                fi
            fi
        fi

        # Poll readiness with progress every 30s — kubectl wait is completely silent during its timeout,
        # making the deploy appear frozen. This loop shows the user the script is still running. 20×30s=600s.
        echo 'Polling PostgreSQL pod readiness (up to 600s)...'
        PG_READY=false
        for i in \$(seq 1 20); do
            READY=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
                -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null || echo '')
            if [ \"\$READY\" = 'True' ]; then
                echo \"PostgreSQL pod is Ready (attempt \$i/20).\"
                PG_READY=true
                break
            fi
            PHASE=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
                --no-headers 2>/dev/null | awk '{print \$3}' || echo 'not found')
            echo \"  Attempt \$i/20 — PostgreSQL not Ready yet (phase: \$PHASE) — waiting 30s...\"
            # On attempt 1, print cluster state so the deploy log immediately shows why the pod is Pending
            if [ \"\$i\" -eq 1 ] && [ \"\$PHASE\" = 'Pending' ]; then
                echo '--- Node status ---'
                kubectl get nodes --no-headers 2>/dev/null || true
                echo '--- PVC status ---'
                kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace 2>/dev/null || true
                echo '--- Pod events (last 8 lines) ---'
                kubectl describe pod airflow-postgresql-0 -n airflow-my-namespace 2>/dev/null \
                    | grep -A 10 'Events:' | tail -8 || true
            fi
            # On attempt 3, if PVC still not Bound after 90s, force-reprovision to recover from race conditions
            if [ \"\$i\" -eq 3 ] && [ \"\$PHASE\" = 'Pending' ]; then
                PVC_PHASE_NOW=\$(kubectl get pvc data-airflow-postgresql-0 -n airflow-my-namespace \
                    -o jsonpath='{.status.phase}' 2>/dev/null || echo '')
                if [ \"\$PVC_PHASE_NOW\" != 'Bound' ]; then
                    echo \"Force-reprovisioning: PVC phase is '\$PVC_PHASE_NOW' after 3 attempts — deleting pod + PVC to restart storage binding...\"
                    kubectl delete pod airflow-postgresql-0 -n airflow-my-namespace \
                        --force --grace-period=0 2>/dev/null || true
                    kubectl delete pvc data-airflow-postgresql-0 -n airflow-my-namespace \
                        --ignore-not-found=true 2>/dev/null || true
                    sleep 5  # let StatefulSet and K3s local-path provisioner reconcile
                fi
            fi
            sleep 30
        done

        if [ \"\$PG_READY\" = false ]; then
            # Pod stuck after 600s — readiness probe never recovered; restart to unstick it
            echo 'WARNING: PostgreSQL not Ready after 600s — restarting pod to force a clean recovery...'
            kubectl describe pod airflow-postgresql-0 -n airflow-my-namespace 2>/dev/null | tail -30 || true
            kubectl delete pod airflow-postgresql-0 -n airflow-my-namespace --ignore-not-found=true
            # Wait for old pod to be fully gone before watching for the new one
            kubectl wait --for=delete pod/airflow-postgresql-0 \
                -n airflow-my-namespace --timeout=30s 2>/dev/null || true
            # Poll the restarted pod — another silent kubectl wait here would appear frozen again. 10×30s=300s.
            echo 'Polling restarted PostgreSQL pod readiness (up to 300s)...'
            PG_READY2=false
            for i in \$(seq 1 10); do
                READY=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
                    -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null || echo '')
                if [ \"\$READY\" = 'True' ]; then
                    echo \"PostgreSQL pod is Ready after restart (attempt \$i/10).\"
                    PG_READY2=true
                    break
                fi
                PHASE=\$(kubectl get pod airflow-postgresql-0 -n airflow-my-namespace \
                    --no-headers 2>/dev/null | awk '{print \$3}' || echo 'not found')
                [ \$i -lt 10 ] && echo \"  Attempt \$i/10 — PostgreSQL still starting (phase: \$PHASE) — waiting 30s...\" && sleep 30
            done
            if [ \"\$PG_READY2\" = false ]; then
                echo 'ERROR: PostgreSQL pod did not become Ready even after restart.'
                kubectl describe pod airflow-postgresql-0 -n airflow-my-namespace 2>/dev/null | tail -30 || true
                exit 1
            fi
        fi
    "

    # Phase 2: poll migration job for both Complete and Failed conditions.
    # kubectl wait --for=condition=complete only watches for success — it completely ignores Failed
    # jobs, causing a silent 600s stall (see 2026-04-13 timeout incident). This polling loop checks
    # both outcomes every 10s. If the job already failed (PostgreSQL wasn't ready in time for its
    # retries), it deletes the failed job and recreates it from the Helm template — PostgreSQL is
    # confirmed ready at this point, so the fresh job should succeed on its first attempt.
    # If the job is gone (already cleaned up from a prior successful deploy), skip and continue.
    ssh "$EC2_HOST" "
        if kubectl get job airflow-run-airflow-migrations -n airflow-my-namespace \
                --ignore-not-found --no-headers 2>/dev/null | grep -q .; then
            echo 'Migration job found — polling for completion (up to 900s)...'
            RETRIED=false
            POD_RESTARTED=false
            for i in \$(seq 1 90); do
                STATUS=\$(kubectl get job airflow-run-airflow-migrations -n airflow-my-namespace \
                    -o jsonpath='{.status.conditions[?(@.status==\"True\")].type}' 2>/dev/null || echo '')
                if echo \"\$STATUS\" | grep -q 'Complete'; then
                    echo 'Migration job complete.'
                    exit 0
                fi
                if echo \"\$STATUS\" | grep -q 'Failed'; then
                    if [ \"\$RETRIED\" = true ]; then
                        echo 'ERROR: Recreated migration job also failed.'
                        kubectl logs job/airflow-run-airflow-migrations -n airflow-my-namespace --tail=50 2>/dev/null || true
                        exit 1
                    fi
                    echo 'Migration job failed (likely started before PostgreSQL was ready).'
                    echo 'Deleting failed job and recreating from Helm template...'
                    kubectl delete job airflow-run-airflow-migrations -n airflow-my-namespace --ignore-not-found=true
                    helm template airflow apache-airflow/airflow \
                        -n airflow-my-namespace \
                        --version 1.20.0 \
                        --set images.airflow.tag=$BUILD_TAG \
                        -f $EC2_HELM_PATH/values.yaml \
                        -s templates/jobs/migrate-database-job.yaml \
                        | kubectl apply -f -
                    RETRIED=true
                    echo 'Retry job created — continuing to poll...'
                fi
                # Detect pod stuck in an unrecoverable image error — fail fast instead of waiting 900s
                MIG_POD=\$(kubectl get pods -n airflow-my-namespace \
                    -l job-name=airflow-run-airflow-migrations --no-headers 2>/dev/null | awk '{print \$1}' | head -1)
                if [ -n \"\$MIG_POD\" ]; then
                    WAIT_REASON=\$(kubectl get pod \"\$MIG_POD\" -n airflow-my-namespace \
                        -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || echo '')
                    if echo \"\$WAIT_REASON\" | grep -qE 'CrashLoopBackOff|ErrImageNeverPull|ImagePullBackOff'; then
                        echo \"Migration pod stuck in \$WAIT_REASON — printing logs and forcing recreation...\"
                        kubectl logs \"\$MIG_POD\" -n airflow-my-namespace --tail=20 2>/dev/null || true
                        if [ \"\$RETRIED\" = false ]; then
                            kubectl delete job airflow-run-airflow-migrations -n airflow-my-namespace --ignore-not-found=true
                            helm template airflow apache-airflow/airflow \
                                -n airflow-my-namespace \
                                --version 1.20.0 \
                                --set images.airflow.tag=$BUILD_TAG \
                                -f $EC2_HELM_PATH/values.yaml \
                                -s templates/jobs/migrate-database-job.yaml \
                                | kubectl apply -f -
                            RETRIED=true
                            echo 'Retry job created after image/crash error — continuing to poll...'
                        else
                            echo 'ERROR: Recreated migration job pod also stuck in error state.'
                            exit 1
                        fi
                    fi
                    # If the pod has been Running for 6 minutes with no completion, it is likely hanging
                    # on a stale PostgreSQL lock or connection — restart the pod to get a fresh connection
                    if [ \$i -eq 36 ] && [ \"\$POD_RESTARTED\" = false ]; then
                        POD_PHASE=\$(kubectl get pod \"\$MIG_POD\" -n airflow-my-namespace \
                            --no-headers 2>/dev/null | awk '{print \$3}' || echo '')
                        if [ \"\$POD_PHASE\" = 'Running' ]; then
                            echo \"Migration pod has been Running for 360s with no completion — restarting pod to clear stale DB lock...\"
                            kubectl logs \"\$MIG_POD\" -n airflow-my-namespace --tail=20 2>/dev/null || true
                            kubectl delete pod \"\$MIG_POD\" -n airflow-my-namespace \
                                --force --grace-period=0 2>/dev/null || true
                            POD_RESTARTED=true
                            echo 'Pod restarted — Kubernetes will create a replacement, continuing to poll...'
                        fi
                    fi
                fi
                # Print progress every 3 iterations (every ~30s) so the terminal doesn't appear frozen
                [ \$(( i % 3 )) -eq 0 ] && echo \"  Still waiting for migration job (attempt \$i/90, \${i}0s elapsed)...\"
                sleep 10
            done
            echo 'ERROR: Migration job did not complete within 900s.'
            kubectl describe job airflow-run-airflow-migrations -n airflow-my-namespace 2>/dev/null | tail -20 || true
            kubectl logs job/airflow-run-airflow-migrations -n airflow-my-namespace --tail=30 2>/dev/null || true
            exit 1
        else
            echo 'Migration job not found — migrations already complete from a prior deploy, skipping.'
        fi
    "
}

step_verify_airflow_image() {
    echo "=== Step 7a: Ensuring airflow image is still in K3S containerd ==="
    # K3S can automatically delete the 3.3 GiB Airflow image to free disk space if no containers are actively
    # using it and disk usage goes above ~85%. This can happen during the ~20 min gap between building the image
    # and restarting the Airflow pods, if the api-server init containers finish and the pods crash in the meantime.
    # Docker still has the image (we never prune it from Docker), so re-importing into K3S is fast.
    ssh "$EC2_HOST" "
        if sudo k3s ctr images list | grep -q 'airflow-dbt:$BUILD_TAG'; then
            echo 'airflow-dbt:$BUILD_TAG confirmed present in K3S containerd'
        else
            echo 'airflow-dbt:$BUILD_TAG not found — GC likely evicted it. Re-importing from Docker store...'
            docker save airflow-dbt:$BUILD_TAG | sudo k3s ctr images import -
            echo 'Re-import complete. Verifying...'
            sudo k3s ctr images list | grep airflow-dbt
        fi
    "
}

step_restart_airflow_pods() {
    echo "=== Step 7: Restarting Airflow pods to prevent stale DAG cache ==="
    # WHY this step is needed:
    #   After syncing new DAG files to EC2, the Airflow pods can hold a stale cached view of the
    #   /opt/airflow/dags/ folder. The DAG Processor pod in particular can still see the old file
    #   list even after the files on disk have been updated. This causes Airflow to flag newly
    #   deployed DAGs as stale and remove them from the UI after ~90 seconds.
    #
    #   Restarting the Scheduler and Processor pods forces Kubernetes to remount the DAG folder
    #   with a fresh view. This is the proven fix from the 2026-03-31 staleness incident.

    # Pre-phase 0: verify any in-progress migration job has completed before restarting pods.
    # Pods' init containers block on wait-for-airflow-migrations — if the schema isn't ready,
    # they loop for 300s per attempt and the deploy times out with Init:0/1.
    # Also catches --dags-only mode, which skips step_helm_upgrade() (and Step 2f) entirely.
    # Uses polling (not kubectl wait) — kubectl wait ignores Failed jobs and would stall for 300s.
    ssh "$EC2_HOST" "
        if kubectl get job airflow-run-airflow-migrations -n airflow-my-namespace \
                --ignore-not-found --no-headers 2>/dev/null | grep -q .; then
            echo 'Migration job still present — verifying completion before restarting pods...'
            for i in \$(seq 1 30); do
                STATUS=\$(kubectl get job airflow-run-airflow-migrations -n airflow-my-namespace \
                    -o jsonpath='{.status.conditions[?(@.status==\"True\")].type}' 2>/dev/null || echo '')
                if echo \"\$STATUS\" | grep -q 'Complete'; then
                    echo 'Migration job confirmed complete.'
                    break
                fi
                if echo \"\$STATUS\" | grep -q 'Failed'; then
                    echo 'ERROR: Migration job has failed — pods would get stuck at Init:0/1. Failing early.'
                    kubectl logs job/airflow-run-airflow-migrations -n airflow-my-namespace --tail=20 2>/dev/null || true
                    exit 1
                fi
                if [ \"\$i\" -eq 30 ]; then
                    echo 'ERROR: Migration job has not completed after 300s — pods would get stuck at Init:0/1. Failing early.'
                    exit 1
                fi
                sleep 10
            done
        fi
    "

    # Pre-phase 1: ensure any Helm rolling update from Step 2d has fully settled before deleting pods.
    # If the update is mid-rollout, two ReplicaSets are active at once; deleting by label hits both.
    # The old-RS pod still has desired=1 until the RS controller scales it to 0; deleting it while
    # desired=1 causes the RS to immediately recreate a pod with the old (already-deleted) image,
    # producing ErrImageNeverPull. Waiting here costs at most 5 minutes.
    # || true: non-fatal — fresh servers may not have a rollout in progress at all.
    ssh "$EC2_HOST" "
        kubectl rollout status deployment/airflow-dag-processor \
            -n airflow-my-namespace --timeout=300s 2>/dev/null || true
    " || true

    # Even after rollout status returns, the old RS pod can still be Terminating for 30-60s.
    # kubectl wait below uses -l component=dag-processor — it watches ALL pods with that label.
    # A Terminating pod from the old RS will never become Ready, causing a guaranteed 600s timeout.
    # Poll until only 1 pod exists so deletion + recreation operate on a clean single-RS state.
    echo "Waiting for any old dag-processor pod to finish terminating..."
    ssh "$EC2_HOST" "
        for i in \$(seq 1 60); do
            COUNT=\$(kubectl get pods -l component=dag-processor \
                -n airflow-my-namespace --no-headers 2>/dev/null | grep -c .)
            [ \"\$COUNT\" -le 1 ] && break
            if [ \"\$i\" -eq 60 ]; then
                # Force-scale ALL old RSes to 0 — after multiple deploys several stale RSes can exist;
                # only the newest RS (last by creation time) should have desired > 0
                RS_LIST=\$(kubectl get rs -l component=dag-processor \
                    -n airflow-my-namespace \
                    --sort-by=.metadata.creationTimestamp \
                    --no-headers \
                    -o custom-columns='NAME:.metadata.name' \
                    2>/dev/null || true)
                TOTAL=\$(echo \"\$RS_LIST\" | grep -c .)
                if [ \"\$TOTAL\" -gt 1 ]; then
                    echo \"\$RS_LIST\" | head -n \$(( TOTAL - 1 )) | while read -r OLD_RS; do
                        [ -z \"\$OLD_RS\" ] && continue
                        echo \"Force-scaling old RS \$OLD_RS to 0 to prevent ErrImageNeverPull on recreation...\"
                        kubectl scale rs \"\$OLD_RS\" --replicas=0 -n airflow-my-namespace 2>/dev/null || true
                    done
                fi
                # Secondary wait: give the pod up to 60s to honour its graceful shutdown after force-scale
                # (default Kubernetes grace period is 30s, so 60s is ample headroom)
                for j in \$(seq 1 12); do
                    NEW_COUNT=\$(kubectl get pods -l component=dag-processor \
                        -n airflow-my-namespace --no-headers 2>/dev/null | grep -c .)
                    [ \"\$NEW_COUNT\" -le 1 ] && break
                    sleep 5
                done
                # Warn only if both the 5-min natural wait AND force-scale failed to clear the old pod
                FINAL_COUNT=\$(kubectl get pods -l component=dag-processor \
                    -n airflow-my-namespace --no-headers 2>/dev/null | grep -c .)
                if [ \"\$FINAL_COUNT\" -gt 1 ]; then
                    echo 'WARNING: still '\$FINAL_COUNT' dag-processor pods after force-scale — Phase A deletion will proceed anyway'
                else
                    echo 'Old dag-processor RS force-scaled to 0 — pod terminated cleanly.'
                fi
            fi
            sleep 5
        done
    " || true

    # Phase A: Delete all three pods in one SSH call — fast, synchronous
    ssh "$EC2_HOST" "
        echo 'Restarting Scheduler pod...' &&
        kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace --ignore-not-found=true &&
        echo 'Restarting DAG Processor pod(s)...' &&
        kubectl delete pod -l component=dag-processor -n airflow-my-namespace --ignore-not-found=true &&
        echo 'Restarting Triggerer pod...' &&
        kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace --ignore-not-found=true &&
        echo 'All three pods deleted — waiting 10s for API server to register termination before watchers start...' &&
        sleep 10
    "

    # Phase B: Wait for all three pods at the same time — total wait time is at most 1000s (scheduler's timeout)
    # instead of up to 50 min if done one at a time. Since all three pods are already deleted and restarting
    # independently, we can wait for them simultaneously.
    # Scheduler: 1000s — startup probe now allows up to 30×60s=1800s (failureThreshold raised from 15→30);
    # with 200m CPU request the scheduler typically starts in <300s, so 1000s is ample.
    # dag-processor/triggerer: 600s — lighter pods without the heavy provider-load startup probe.
    echo "Waiting for Airflow pods to become Ready (parallel)..."
    ssh "$EC2_HOST" "kubectl wait pod/airflow-scheduler-0 -n airflow-my-namespace --for=condition=Ready --timeout=1000s" &
    local sched_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod -l component=dag-processor -n airflow-my-namespace --for=condition=Ready --timeout=600s" &
    local dagproc_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod/airflow-triggerer-0 -n airflow-my-namespace --for=condition=Ready --timeout=600s" &
    local trigger_pid=$!

    # Wait on shorter-timeout pods first (600s each), then scheduler (1000s).
    # OLD ORDER: _wait_bg $sched_pid ran first — if dag-processor/triggerer timed out at 600s,
    # bash stayed blocked inside 'wait $sched_pid' for up to 400s more, freezing the terminal.
    # NEW ORDER: check 600s pods first; if either fails, kill the scheduler wait immediately.
    local dagproc_rc=0 trigger_rc=0 sched_rc=0
    wait "$dagproc_pid" || dagproc_rc=$?
    wait "$trigger_pid" || trigger_rc=$?

    if [ "$dagproc_rc" -ne 0 ] || [ "$trigger_rc" -ne 0 ]; then
        kill "$sched_pid" 2>/dev/null || true  # no point waiting for scheduler — already failing
        [ "$dagproc_rc" -ne 0 ] && {
            echo "✗ dag-processor Ready FAILED — describing pods..."
            ssh "$EC2_HOST" "kubectl get pods -l component=dag-processor -n airflow-my-namespace" || true
            ssh "$EC2_HOST" "kubectl describe pod -l component=dag-processor -n airflow-my-namespace | tail -40" || true
        }
        [ "$trigger_rc" -ne 0 ] && {
            echo "✗ airflow-triggerer-0 Ready FAILED — describing pod..."
            ssh "$EC2_HOST" "kubectl describe pod airflow-triggerer-0 -n airflow-my-namespace | tail -40" || true
        }
        exit 1
    fi

    wait "$sched_pid" || sched_rc=$?
    if [ "$sched_rc" -ne 0 ]; then
        echo "✗ airflow-scheduler-0 Ready FAILED"
        ssh "$EC2_HOST" "kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | tail -50" || true
        ssh "$EC2_HOST" "kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=30 2>/dev/null" || true
        exit 1
    fi
    echo "All Airflow pods Ready."

    # Phase B.5: Poll until scheduler container is exec-able
    # kubectl wait --for=condition=Ready only checks the pod condition — the container runtime needs
    # a few extra seconds before kubectl exec can actually reach the container by name.
    echo "Waiting for scheduler container to accept exec connections..."
    _wait_scheduler_exec

    # Phase B.6: Verify scheduler is running via port 8793 (Airflow 3.x internal execution API server).
    # Port 8974 (Airflow 2.x HTTP health server) no longer exists in Airflow 3.x — curl on 8974 exits 7.
    # pgrep -f 'airflow scheduler' exits 1 — Airflow 3.x scheduler process name doesn't match that pattern.
    # Instead: Airflow 3.x scheduler pods run a uvicorn/FastAPI internal API on port 8793.
    # curl exits 0 for any HTTP response, exits 7 only if nothing is listening — zero Python overhead, no OOM risk.

    # Phase C1: Scheduler health check with retry — confirm port 8793 is accepting connections after exec-readiness.
    # IMPORTANT: ssh command uses '|| exit_code=$?' instead of a bare next-line '$?' capture.
    # deploy.sh sets 'set -euo pipefail', so a bare non-zero ssh exit code triggers immediate script
    # exit before the next line can run — the retry loop was being bypassed entirely on exit 137.
    # The '||' pattern prevents set -e from firing while still capturing the real exit code.
    echo "Verifying scheduler health (with retry)..."
    local dags_ok=0
    for attempt in 1 2 3 4 5; do
        # curl without -f: exits 0 for any HTTP response (200/401/404), exits 7 if port not listening
        local exit_code=0
        ssh "$EC2_HOST" "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- curl -s --max-time 10 -o /dev/null http://localhost:8793/" || exit_code=$?
        if [ $exit_code -eq 0 ]; then
            dags_ok=1
            break
        fi
        # Exit 137 = SIGKILL — curl adds no Python overhead so this means an unrelated container restart mid-check
        if [ $exit_code -eq 137 ]; then
            echo "  Health check attempt $attempt/5 — container was killed (exit 137). Waiting 15s for restart..."
            sleep 15
        else
            echo "  Health check attempt $attempt/5 failed (exit $exit_code) — retrying in 10s..."
            sleep 10
        fi
    done
    if [ "$dags_ok" -eq 0 ]; then
        echo ""
        echo "WARNING: airflow health failed after 5 attempts. Scheduler may not be ready — check scheduler logs."
        # Print recent scheduler logs to surface the actual error without needing to SSH in manually
        ssh "$EC2_HOST" "kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=30 2>/dev/null || true"
    fi

    # Variables (KAFKA_BOOTSTRAP_SERVERS, MLFLOW_TRACKING_URI) are injected via AIRFLOW_VAR_* in values.yaml.
    # kubectl exec airflow variables set OOM-kills (exit 137) the scheduler on Airflow 3.x — importing the
    # full provider stack spikes memory past the 2Gi container limit. Env var injection avoids that entirely.

    # Phase D: Reset Kafka consumer group offsets to latest.
    # After any pod restart or fresh deploy, committed offsets are lost. Both consumer groups use
    # auto_offset_reset="latest" with enable_auto_commit=False (manual commit). Without a committed
    # offset, the consumer seeks to the end of the topic at connect time — after the producer has
    # already published. The consumer polls for 30s, finds nothing, commits nothing, and exits with
    # 0 records. Every subsequent run repeats this cycle silently: dbt and anomaly detection are
    # always skipped. Resetting to --to-latest here positions each group at the current end of the
    # topic so the NEXT message the producer publishes is the one the consumer reads.
    # Note: --to-earliest is NOT used — the weather topic has old corrupt messages near offset 0 that
    # cause JSONDecodeError during deserialization.
    # Note: groups are checked for existence first — calling --reset-offsets on a group that has never
    # connected causes a Java TimeoutException (Kafka can't find a coordinator node for it).
    # IMPORTANT: Phase D runs before Phase C2 (DAG unpause) — Kafka refuses offset resets on "Stable"
    # (active) consumer groups; DAGs must be paused so no consumer is connected during the reset.
    echo "=== Resetting Kafka consumer group offsets to latest ==="
    ssh "$EC2_HOST" "
        # List existing groups before attempting reset — avoids a Java TimeoutException that fires
        # when a group has never connected (no coordinator node assigned for it yet).
        EXISTING=\$(kubectl exec kafka-0 -n kafka -- \
            /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:9092 --list 2>/dev/null || echo '')
        for pair in stocks-consumer-group:stocks-financials-raw weather-consumer-group:weather-hourly-raw; do
            group=\${pair%%:*}
            topic=\${pair##*:}
            if echo \"\$EXISTING\" | grep -q \"^\$group\$\"; then
                kubectl exec kafka-0 -n kafka -- \
                    /opt/kafka/bin/kafka-consumer-groups.sh \
                    --bootstrap-server localhost:9092 \
                    --group \"\$group\" --reset-offsets --to-latest \
                    --topic \"\$topic\" --execute
            else
                echo \"\$group not found — skipping reset (fresh deploy, consumer has not connected yet)\"
            fi
        done
        echo 'Kafka consumer group offsets check complete.'
    " || echo "WARNING: Kafka offset reset failed — run Steps 8 and 10 of RESTORE_VERIFICATION.md manually before triggering pipelines."

    # Phase C2: Unpause consumer DAGs.
    # Airflow registers all new DAGs as paused by default — triggered runs queue but never start until unpaused.
    # We use direct psql (not `airflow dags unpause`) to avoid importing the full provider stack
    # into the scheduler pod, which OOM-kills it (exit 137) the same way `airflow variables set` does.
    echo "=== Unpausing consumer DAGs via PostgreSQL ==="
    ssh "$EC2_HOST" "
        PGPASS=\$(kubectl get secret airflow-postgresql -n airflow-my-namespace \
            -o jsonpath='{.data.postgres-password}' | base64 -d)
        kubectl exec airflow-postgresql-0 -n airflow-my-namespace -- \
            env PGPASSWORD=\"\$PGPASS\" psql -U postgres -d postgres -c \
            \"UPDATE dag SET is_paused = false
              WHERE dag_id IN ('stock_consumer_pipeline', 'weather_consumer_pipeline');\"
    " && echo "Consumer DAGs unpaused." \
      || echo "WARNING: failed to unpause consumer DAGs — unpause manually via Airflow UI before triggering pipelines."
}

step_setup_ml_venv() {
    echo "=== Step 7b: Creating/updating ml-venv in Airflow scheduler pod ==="
    # anomaly_detector.py uses /opt/ml-venv/bin/python directly — this virtual environment must exist before the DAG runs.
    # /opt/ inside the container is temporary — it gets wiped every time the pod restarts — so we rebuild
    # the venv here after every pod restart. This also means any package version changes we make here take
    # effect immediately, without needing to rebuild the Docker image.
    # We create an isolated venv (no --system-site-packages) to avoid conflicts with Airflow's own Python packages.
    # Re-verify exec-readiness — the container may have briefly lost its exec connection since step_restart_airflow_pods ran
    _wait_scheduler_exec

    ssh "$EC2_HOST" "
        # Fast path: use pip show (reads metadata only, no imports) to avoid OOM-killing the scheduler.
        # Importing all 4 ML packages simultaneously in a running scheduler pod spikes ~500-800 MB — enough
        # to exceed the 2 Gi container limit and produce a false exit 137 that triggers an unnecessary rebuild.
        if kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            /opt/ml-venv/bin/pip show mlflow scikit-learn snowflake-connector-python pandas setuptools > /dev/null 2>&1; then
            echo 'ml-venv package check passed (pip show) — skipping reinstall'
            echo 'ml-venv ready at /opt/ml-venv'
        else
            # Fallback: venv is missing or broken (e.g., image mismatch, container corruption) — rebuild
            # --upgrade: idempotent — reinitialises an existing venv dir without wiping site-packages
            echo 'ml-venv missing or broken — rebuilding...' &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                python3 -m venv --upgrade /opt/ml-venv &&

            # Install one package at a time — avoids a single large pip resolver memory spike that OOM-kills the container
            # chardet<6: version 6+ causes a version mismatch warning from requests; pin to match Dockerfile
            echo 'Installing ML packages into ml-venv (one at a time to reduce memory pressure)...' &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"mlflow==2.15.1\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"scikit-learn==1.5.2\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"pandas==2.2.2\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"snowflake-connector-python==3.10.1\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"setuptools<75\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"requests>=2.32.0\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"chardet>=3.0.2,<6\" &&

            # Confirm all packages are present after rebuild
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip show mlflow scikit-learn snowflake-connector-python pandas setuptools > /dev/null &&

            echo 'ml-venv ready at /opt/ml-venv'
        fi
    " || {
        echo ""
        echo "WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved."
        echo "If pip install keeps OOM-killing the container, a full redeploy (Docker image rebuild) is required."
        echo "Re-run without a full redeploy: ./scripts/deploy.sh --fix-ml-venv"
        echo "Diagnose with: kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/pip list"
    }
}
