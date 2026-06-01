#!/bin/bash
# Module: pgvector — deploys the vector database pod that future AI features will read and write.
#
# pgvector is a Postgres database with an extension for storing "meaning fingerprints" (embeddings)
# alongside regular text. Once this pod is running, the Airflow scheduler can insert chunks of SEC
# filings or weather data with their embeddings, then later retrieve whichever chunks are most
# similar in meaning to an arbitrary question — without needing exact keyword matches.
#
# This module mirrors the mlflow.sh pattern:
#   prune old images → docker pull (with 3 retries) → import into K3S containerd → sync manifests
#   → apply to cluster → poll until the pod is healthy, recovering from disk-pressure mid-rollout.
#
# Sourced by deploy.sh; all variables from common.sh are available here.
# Only called when GENAI_ENABLED=true — the caller gates the invocation.

PGVECTOR_IMAGE="pgvector/pgvector:pg16"

step_deploy_pgvector() {
    echo "=== Step 2b7: Syncing pgvector manifests to EC2 ==="
    # ensure the destination directory exists before rsync runs
    ssh "$EC2_HOST" "mkdir -p $EC2_HOME/infra/genai/pgvector"
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/infra/genai/pgvector/" "$EC2_HOST:$EC2_HOME/infra/genai/pgvector/"

    echo "=== Step 2b7a: Importing pgvector image into K3S containerd ==="
    # Same import pattern as MLflow — pull via Docker (layer-cached), pipe into K3S's own image store.
    # imagePullPolicy: Never in the Deployment means K3S will never try to pull from the internet.
    # flock shares one lock file with the MLflow job (which runs at the same time on the same Docker store),
    # so the two jobs take turns and neither's cleanup can delete an image the other is still downloading.
    ssh "$EC2_HOST" "
        echo 'Pruning old pgvector images from K3S containerd to free ephemeral storage...' &&
        sudo k3s ctr images ls | grep 'pgvector' | awk '{print \$1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true &&
        echo 'Pruning dangling Docker images to free disk space...' &&
        for _p in 1 2 3 4 5; do
            out=\$(flock -w 600 /tmp/docker-content-store.lock docker image prune -f 2>&1) && echo \"\$out\" && break
            echo \"\$out\" | grep -q 'prune operation is already running' \
                && echo \"Prune already running (attempt \$_p/5) — waiting 10s...\" && sleep 10 \
                || { echo \"\$out\"; break; }
        done || true
    "

    # Retry the pull up to 3 times — transient network or overlay-extract failures on a loaded node
    for _pull_attempt in 1 2 3; do
        if ssh "$EC2_HOST" "
            echo 'Pulling pgvector image via Docker (attempt $_pull_attempt/3)...' &&
            # Take the shared Docker-store lock before pulling so the MLflow job's cleanup can't wipe this download mid-flight.
            flock -w 600 /tmp/docker-content-store.lock docker pull $PGVECTOR_IMAGE
        "; then
            break
        fi
        if [ "$_pull_attempt" -lt 3 ]; then
            echo "pgvector docker pull attempt $_pull_attempt failed — removing partial image and retrying in 15s..."
            # Locked too: deleting an image also touches the shared store, so it must wait its turn like the prune/pull above.
            ssh "$EC2_HOST" "flock -w 600 /tmp/docker-content-store.lock docker rmi '$PGVECTOR_IMAGE' 2>/dev/null || true"
            sleep 15
        else
            echo "✗ pgvector docker pull failed after 3 attempts"
            return 1
        fi
    done

    # Shared helper: hands the locally-pulled image over to K3S's own image store so the pod can use it
    # without ever going to the internet (matches imagePullPolicy: Never). Frees ~300 MB of Docker cache after.
    import_image_to_k3s "$PGVECTOR_IMAGE" "pgvector"

    # Drop Docker's copy of the image now that K3S has its own (mirrors the MLflow pattern)
    # Lock the shared Docker store while cleaning up — the MLflow job may still be pulling and we must not delete its layers.
    ssh "$EC2_HOST" "echo 'Pruning Docker image layer cache after pgvector K3S import...' && flock -w 600 /tmp/docker-content-store.lock docker image prune -af --filter 'until=1h' 2>&1 | tail -5" || true

    echo "=== Step 2b7b: Deploying pgvector to K3s (safe to run multiple times) ==="
    _ensure_kubectl_accessible
    ssh "$EC2_HOST" "
        echo '--- Node taints and pressure conditions pre-pgvector-rollout ---'
        kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'
        kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: {range .status.conditions[*]}{.type}={.status}  {end}{\"\n\"}{end}'
    "
    ssh "$EC2_HOST" "
        # Ensure the data directory exists on the EC2 host — the hostPath PV points here
        mkdir -p /home/ubuntu/pgvector-data

        # Apply in dependency order: storage → claim → init SQL → pod → service
        kubectl apply -f $EC2_HOME/infra/genai/pgvector/pv-pgvector.yaml \
        && kubectl apply -f $EC2_HOME/infra/genai/pgvector/pvc-pgvector.yaml -n airflow-my-namespace \
        && kubectl apply -f $EC2_HOME/infra/genai/pgvector/configmap-init-sql.yaml -n airflow-my-namespace \
        && kubectl apply -f $EC2_HOME/infra/genai/pgvector/deployment-pgvector.yaml -n airflow-my-namespace \
        && kubectl apply -f $EC2_HOME/infra/genai/pgvector/service-pgvector.yaml -n airflow-my-namespace \
        && echo 'pgvector manifests applied.'
    "

    # Before the rollout, free up disk space and clear the node's "disk-pressure" flag.
    # (When the EC2 disk gets too full, K3S automatically sets that flag, which blocks any new pod
    #  from starting. This server has a small disk, so we proactively clear it. Same pattern as MLflow.)
    _ensure_disk_space
    _remove_disk_pressure_taint

    echo 'Polling pgvector rollout (24 × 15s = 360s)...'
    ROLLOUT_OK=false
    for _i in $(seq 1 24); do
        _READY=$(ssh "$EC2_HOST" "kubectl get deployment pgvector -n airflow-my-namespace \
            -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0")
        if [ "${_READY:-0}" -ge 1 ]; then ROLLOUT_OK=true; break; fi
        # Check whether the pod is stuck waiting to start ("Pending") or got kicked off the server ("Evicted").
        # On this small server a full disk is the usual cause, so we free space and clear the flag, then retry.
        _BAD=$(ssh "$EC2_HOST" "kubectl get pods -n airflow-my-namespace -l app=pgvector \
            --no-headers 2>/dev/null | grep -cE 'Pending|Evicted'" 2>/dev/null || echo 0)
        if [ "${_BAD:-0}" -gt 0 ]; then
            echo "  pgvector pod(s) Pending/Evicted — refreshing disk space and taint (attempt $_i/24)..."
            _ensure_disk_space
            _remove_disk_pressure_taint
            ssh "$EC2_HOST" "kubectl delete pods -n airflow-my-namespace -l app=pgvector \
                --field-selector=status.phase=Failed --ignore-not-found=true 2>/dev/null" || true
        else
            echo "  Attempt $_i/24 — pgvector availableReplicas=${_READY:-0}, waiting 15s..."
        fi
        # "ErrImageNeverPull" means the pod can't find the database image on the server. We set
        # imagePullPolicy: Never, so K3S won't download it from the internet — if K3S's automatic
        # disk cleanup deleted the image to free space, we must re-import our local copy (below).
        _IMG_BAD=$(ssh "$EC2_HOST" "kubectl get pods -n airflow-my-namespace -l app=pgvector \
            -o jsonpath='{.items[*].status.containerStatuses[*].state.waiting.reason}' 2>/dev/null \
            | grep -ow 'ErrImageNeverPull' | head -1" 2>/dev/null || echo '')
        if [ -n "$_IMG_BAD" ]; then
            echo "  pgvector pod stuck in ErrImageNeverPull — re-importing image into K3S containerd..."
            ssh "$EC2_HOST" "
                _tmp=\$(mktemp /tmp/k3s-import-XXXXXX.tar)
                docker save '$PGVECTOR_IMAGE' > \"\$_tmp\" \
                    && sudo k3s ctr images import \"\$_tmp\" \
                    && rm -f \"\$_tmp\" \
                    && echo 'pgvector image re-import complete.' \
                    || { rm -f \"\$_tmp\" 2>/dev/null; echo 'WARNING: pgvector image re-import failed'; }
            "
        fi
        sleep 15
    done

    if [ "$ROLLOUT_OK" = false ]; then
        echo 'ERROR: pgvector rollout timed out. Diagnosing...'
        ssh "$EC2_HOST" "
            echo '--- pgvector pod status ---'
            kubectl get pods -n airflow-my-namespace -l app=pgvector
            echo '--- pgvector pod describe (last 30 lines) ---'
            kubectl describe pod -n airflow-my-namespace -l app=pgvector | tail -30
            echo '--- pgvector pod logs (last 30 lines) ---'
            kubectl logs -n airflow-my-namespace -l app=pgvector --tail=30 2>/dev/null \
                || echo '(no logs — pod may not have started)'
        "
        _ensure_disk_space
        return 1
    fi

    echo '=== pgvector pod is Running ==='

    # Align the database role's password with the secret, and ensure the schema exists — both idempotent,
    # both guarding against the persistent-volume "only runs on first init" gotcha (see each function).
    _sync_pgvector_password
    _ensure_pgvector_schema
}

# Apply the init SQL to the LIVE database every deploy so the `chunks` table + indexes always exist.
#
# WHY this is needed: the official Postgres image only runs /docker-entrypoint-initdb.d/*.sql the FIRST
# time it initializes an EMPTY data directory. The data lives on a persistent hostPath, so if the data
# dir already existed when the `chunks` schema was introduced (or was ever initialized empty), the table
# was never created and the ingest fails with 'relation "chunks" does not exist'. The init SQL is fully
# idempotent (CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS), so re-applying the mounted file here every
# deploy guarantees the schema is present without touching any existing rows.
_ensure_pgvector_schema() {
    echo "=== Step 2b7d: Ensuring pgvector schema exists (idempotent) ==="
    local _pod
    _pod=$(ssh "$EC2_HOST" "kubectl get pod -l app=pgvector -n airflow-my-namespace \
        -o jsonpath='{.items[0].metadata.name}'" 2>/dev/null || true)
    if [ -z "$_pod" ]; then
        echo "WARNING: could not find the pgvector pod — skipping schema check."
        return 0
    fi
    # Re-run the mounted init SQL against the live DB over the local socket (trust auth as the superuser).
    # bash -c runs IN the pod so $POSTGRES_USER/$POSTGRES_DB expand from the pod env.
    ssh "$EC2_HOST" "kubectl exec '$_pod' -n airflow-my-namespace -- \
        bash -c 'psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -f /docker-entrypoint-initdb.d/init.sql'" \
        && echo "pgvector schema ensured." \
        || echo "WARNING: pgvector schema ensure failed — the next ingest run may fail."
}

# Force the pgvector role's password to match the secret's PGVECTOR_PASSWORD.
#
# WHY this is needed: Postgres only applies POSTGRES_PASSWORD when it FIRST initializes an empty data
# directory. The data lives on a persistent hostPath (/home/ubuntu/pgvector-data), so on every later
# start Postgres ignores POSTGRES_PASSWORD. If the secret's password is ever rotated — or PGVECTOR_PASSWORD
# was set differently from the POSTGRES_PASSWORD the DB was born with — the live role keeps the OLD
# password while the Python client connects with PGVECTOR_USER/PGVECTOR_PASSWORD and gets
# "password authentication failed for user". Running this ALTER every deploy means the role password can
# never drift from the secret. It is idempotent and safe to repeat.
#
# HOW it stays secret-safe: psql runs over the local unix socket as the bootstrap superuser (trust auth,
# no password needed) and reads BOTH the target user and the new password from the pod's own environment
# via \getenv — so the password is never placed on a command line, in shell history, or in any log.
_sync_pgvector_password() {
    echo "=== Step 2b7c: Syncing pgvector role password to the secret (idempotent) ==="
    local _pod
    _pod=$(ssh "$EC2_HOST" "kubectl get pod -l app=pgvector -n airflow-my-namespace \
        -o jsonpath='{.items[0].metadata.name}'" 2>/dev/null || true)
    if [ -z "$_pod" ]; then
        echo "WARNING: could not find the pgvector pod — skipping password sync."
        return 0
    fi
    # bash -c runs IN the pod so $POSTGRES_USER/$POSTGRES_DB expand from the pod env (not the EC2 shell);
    # the SQL on stdin uses \getenv to pull PGVECTOR_USER/PGVECTOR_PASSWORD from that same pod env.
    ssh "$EC2_HOST" "kubectl exec -i '$_pod' -n airflow-my-namespace -- \
        bash -c 'psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" <<'SQL' \
        && echo "pgvector role password synced to the secret." \
        || echo "WARNING: pgvector password sync failed — the next ingest run may hit auth errors."
\getenv pw PGVECTOR_PASSWORD
\getenv u PGVECTOR_USER
ALTER USER :"u" PASSWORD :'pw';
SQL
}
