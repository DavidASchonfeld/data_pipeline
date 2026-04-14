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
        echo 'Waiting for PostgreSQL pod to be Ready (up to 300s)...'
        kubectl wait pod/airflow-postgresql-0 \
            -n airflow-my-namespace \
            --for=condition=Ready \
            --timeout=300s || {
            echo 'ERROR: PostgreSQL pod did not become Ready within 300s.'
            kubectl describe pod airflow-postgresql-0 -n airflow-my-namespace 2>/dev/null | tail -20 || true
            exit 1
        }
        echo 'PostgreSQL pod is Ready.'
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
            echo 'Migration job found — polling for completion (up to 600s)...'
            RETRIED=false
            for i in \$(seq 1 60); do
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
                sleep 10
            done
            echo 'ERROR: Migration job did not complete within 600s.'
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
                echo 'WARNING: still '\$COUNT' dag-processor pods after 5 min — old RS may not have scaled down'
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
                    sleep 5
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
