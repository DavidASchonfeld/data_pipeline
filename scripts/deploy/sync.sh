#!/bin/bash
# Module: sync — rsync file transfers and K8s secret/manifest application.
# Sourced by deploy.sh; all variables from common.sh are available here.

# rsync flags used throughout:
# -a: archive mode (preserves permissions and timestamps)
# -v: verbose (shows which files were transferred)
# -z: compress data in transit
# --progress: shows per-file progress bar and transfer speed
# Note: rsync does not respect .gitignore, so files like api_key.py, db_config.py, and constants.py are synced intentionally

step_sync_dags() {
    echo "=== Step 2: Syncing DAG files to EC2 ==="
    # Trailing "/" on source means "sync contents of folder", not the folder itself
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/airflow/dags/" "$EC2_HOST:$EC2_DAG_PATH/"
}

step_sync_helm_dockerfile() {
    echo "=== Step 2b: Syncing Helm values to EC2 ==="
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/airflow/helm/values.yaml" "$EC2_HOST:$EC2_HELM_PATH/"

    echo "=== Step 2b1: Syncing Airflow Dockerfile to EC2 ==="
    # Sync the Dockerfile so the image can be built on EC2 (image is built and loaded directly into K3S — it's never pushed to ECR)
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/airflow/docker/" "$EC2_HOST:$EC2_HOME/airflow/docker/"
}

step_sync_manifests_secrets() {
    # Verify K3s API server is accepting connections before kubectl apply runs
    _wait_k3s_api_ready
    echo "=== Step 2c: Syncing Kubernetes manifests to EC2 ==="
    # These copies let you run kubectl commands directly on EC2 if you ever need to
    # (Git is still the master copy — these are just for convenience on the EC2 side)
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/airflow/manifests/" "$EC2_HOST:$EC2_HOME/airflow/manifests/"
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/dashboard/manifests/" "$EC2_HOST:$EC2_HOME/dashboard/manifests/"

    echo "=== Step 2c1: Applying K8s secrets (credentials) ==="
    # Apply Snowflake and database credential secrets to both airflow-my-namespace and default namespaces.
    # Must run before Step 2d (Helm upgrade) so pods can read their environment variables when they start.
    # These secret files are gitignored and never committed — they only exist locally and on EC2.
    ssh "$EC2_HOST" "
        if [ -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml ]; then
            echo 'Applying Snowflake credentials to airflow-my-namespace...' &&
            kubectl apply -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml -n airflow-my-namespace &&
            echo 'Applying Snowflake credentials to default namespace (for Flask pod)...' &&
            kubectl apply -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml -n default
        else
            echo 'Note: snowflake-secret.yaml not found — skipping (first deploy before secret created).'
        fi
    "

    echo "=== Step 2c1a: Patching SNOWFLAKE_ROLE + AIRFLOW_CONN_SNOWFLAKE_DEFAULT into snowflake-credentials secret ==="
    # SNOWFLAKE_ROLE is not stored in snowflake-secret.yaml, so we add it here on every deploy.
    # anomaly_detector.py reads this value from the environment at runtime.
    #
    # AIRFLOW_CONN_SNOWFLAKE_DEFAULT is also injected here.
    # Airflow 3 reads AIRFLOW_CONN_<CONN_ID> env vars at startup and auto-registers the connection —
    # this means SnowflakeHook(snowflake_conn_id="snowflake_default") works on a fresh install
    # without any manual setup in the Airflow UI.
    #
    # The JSON patch `add` operation creates the key if it doesn't exist, or updates it if it does — safe to run every time.
    ssh "$EC2_HOST" "
        # Read Snowflake credentials from the already-applied snowflake-credentials secret
        SF_ACCOUNT=\$(kubectl get secret snowflake-credentials -n airflow-my-namespace -o jsonpath='{.data.SNOWFLAKE_ACCOUNT}' | base64 -d) &&
        SF_USER=\$(kubectl get secret snowflake-credentials -n airflow-my-namespace -o jsonpath='{.data.SNOWFLAKE_USER}' | base64 -d) &&

        # Build the Airflow connection in JSON format — SnowflakeHook 6.x reads 'account' from extra,
        # not from the URI host field. Using JSON ensures account is correctly set in extra.
        # 'private_key_file' tells the hook to authenticate with RSA key-pair auth — no password is sent over the wire.
        # The path below is the in-pod mount point for the snowflake-rsa-key secret (see Step 2c2b).
        CONN_URI=\"{\\\"conn_type\\\": \\\"snowflake\\\", \\\"login\\\": \\\"\$SF_USER\\\", \\\"extra\\\": {\\\"account\\\": \\\"\$SF_ACCOUNT\\\", \\\"private_key_file\\\": \\\"/secrets/snowflake/rsa_key.p8\\\", \\\"database\\\": \\\"PIPELINE_DB\\\", \\\"schema\\\": \\\"RAW\\\", \\\"warehouse\\\": \\\"PIPELINE_WH\\\", \\\"role\\\": \\\"PIPELINE_ROLE\\\"}}\" &&

        ROLE_B64=\$(printf 'PIPELINE_ROLE' | base64 -w0) &&
        CONN_B64=\$(printf '%s' \"\$CONN_URI\" | base64 -w0) &&

        kubectl patch secret snowflake-credentials -n airflow-my-namespace \
            --type=json \
            -p=\"[
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/SNOWFLAKE_ROLE\\\",\\\"value\\\":\\\"\$ROLE_B64\\\"},
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/AIRFLOW_CONN_SNOWFLAKE_DEFAULT\\\",\\\"value\\\":\\\"\$CONN_B64\\\"}
            ]\" &&
        kubectl patch secret snowflake-credentials -n default \
            --type=json \
            -p=\"[
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/SNOWFLAKE_ROLE\\\",\\\"value\\\":\\\"\$ROLE_B64\\\"},
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/AIRFLOW_CONN_SNOWFLAKE_DEFAULT\\\",\\\"value\\\":\\\"\$CONN_B64\\\"}
            ]\" &&
        echo 'SNOWFLAKE_ROLE + AIRFLOW_CONN_SNOWFLAKE_DEFAULT patched into both namespaces.'
    "

    echo "=== Step 2c1b: Creating flask-app-secrets K8s secret ==="
    # Flask requires a secret key for session/cookie security; /validation uses VALIDATION_USER/PASS for HTTP Basic Auth.
    # --dry-run=client -o yaml | kubectl apply is idempotent: creates the secret if absent, updates it if present.
    ssh "$EC2_HOST" "
        kubectl create secret generic flask-app-secrets \
            -n default \
            --from-literal=FLASK_SECRET_KEY='${FLASK_SECRET_KEY}' \
            --from-literal=VALIDATION_USER='${VALIDATION_USER}' \
            --from-literal=VALIDATION_PASS='${VALIDATION_PASS}' \
            --dry-run=client -o yaml | kubectl apply -f -
    "

    echo "=== Step 2c2: Syncing dbt profiles secret to EC2 ==="
    # profiles.yml is gitignored (contains dbt connection config referencing Snowflake env vars).
    # scp copies the file to EC2, then kubectl creates or updates the dbt-profiles secret (safe to run multiple times).
    # The secret is mounted into the Airflow scheduler and workers at /dbt/ (configured in values.yaml).
    # Airflow tasks point dbt to that folder by setting DBT_PROFILES_DIR=/dbt.
    if [ -f "$PROJECT_ROOT/profiles.yml" ]; then
        scp "$PROJECT_ROOT/profiles.yml" "$EC2_HOST:$EC2_HOME/profiles.yml"
        # apply_k8s_secret handles --dry-run=client -o yaml | kubectl apply (idempotent create/update)
        apply_k8s_secret airflow-my-namespace dbt-profiles "--from-file=profiles.yml=$EC2_HOME/profiles.yml"
    else
        echo "Note: profiles.yml not found locally — skipping (create it first if dbt is not yet set up)."
    fi

    echo "=== Step 2c2b: Syncing Snowflake RSA private-key secret to EC2 ==="
    # The .p8 file holds the private key PIPELINE_USER uses for key-pair auth (replaces password).
    # Path on the local Mac comes from SNOWFLAKE_PRIVATE_KEY_PATH in .env.deploy; same env var name
    # is also set inside the pod (to /secrets/snowflake/rsa_key.p8) by snowflake-secret.yaml.
    # The file is gitignored — it lives only on the Mac and on EC2.
    # Secret is applied to BOTH namespaces because the dashboard pod runs in 'default' and Airflow runs in airflow-my-namespace.
    if [ -n "${SNOWFLAKE_PRIVATE_KEY_PATH:-}" ] && [ -f "$SNOWFLAKE_PRIVATE_KEY_PATH" ]; then
        scp "$SNOWFLAKE_PRIVATE_KEY_PATH" "$EC2_HOST:$EC2_HOME/pipeline_user_rsa.p8"
        # Both pods mount the secret at /secrets/snowflake/rsa_key.p8 (see helm values.yaml + pod-flask.yaml)
        apply_k8s_secret airflow-my-namespace snowflake-rsa-key "--from-file=rsa_key.p8=$EC2_HOME/pipeline_user_rsa.p8"
        apply_k8s_secret default              snowflake-rsa-key "--from-file=rsa_key.p8=$EC2_HOME/pipeline_user_rsa.p8"
    else
        echo "Note: SNOWFLAKE_PRIVATE_KEY_PATH not set or file missing — skipping (RSA key-pair auth not configured yet)."
    fi

    # genai: apply the AI-layer credentials secret when the feature is enabled
    if [ "${GENAI_ENABLED:-false}" = "true" ]; then
        echo "=== Step 2c2c: Applying GenAI credentials secret ==="
        # Sync the secret file to EC2 first — it lives only on the Mac and the server, never in git
        if [ -f "$PROJECT_ROOT/infra/genai/secrets/genai-secrets.yaml" ]; then
            # ensure the destination directory exists on EC2 before rsync runs
            ssh "$EC2_HOST" "mkdir -p $EC2_HOME/infra/genai/secrets"
            rsync $RSYNC_FLAGS "$PROJECT_ROOT/infra/genai/secrets/genai-secrets.yaml" \
                "$EC2_HOST:$EC2_HOME/infra/genai/secrets/genai-secrets.yaml"
            # apply to airflow-my-namespace so the scheduler pod can read the API key
            ssh "$EC2_HOST" "
                kubectl apply -f $EC2_HOME/infra/genai/secrets/genai-secrets.yaml -n airflow-my-namespace &&
                echo 'GenAI credentials secret applied to airflow-my-namespace.'
            "
        else
            echo "Note: infra/genai/secrets/genai-secrets.yaml not found — skipping."
            echo "      Create it from the template at infra/genai/secrets/genai-secrets.yaml.template."
        fi
    fi

    echo "=== Step 2c3: Deleting stale Airflow migration Job ==="
    # This Job was created before Helm was managing it, so it's missing the labels Helm expects to see.
    # With the old setup (useHelmHooks:true), Helm created this Job automatically. Now that we've switched
    # to useHelmHooks:false, Helm tries to take ownership of the existing Job and fails.
    # Safe to delete — the database migration already ran, and Helm will recreate the Job on the next upgrade if it needs to.
    ssh "$EC2_HOST" "kubectl delete job airflow-run-airflow-migrations -n airflow-my-namespace --ignore-not-found=true \
        && echo 'Migration Job cleared (safe to run multiple times).'"
}
