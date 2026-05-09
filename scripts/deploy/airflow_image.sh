#!/bin/bash
# Module: airflow_image — Docker build of the custom Airflow+dbt image and K3S import.
# Sourced by deploy.sh; BUILD_TAG must be set in deploy.sh before calling step_build_airflow_image.

step_build_airflow_image() {
    echo "=== Step 2b2: Building Airflow+dbt image and importing into K3S ==="
    # WHY build on EC2 instead of pushing to ECR:
    #   The custom airflow-dbt image only ever needs to exist on this one EC2 instance.
    #   ECR would add ~$0.15/month storage cost for no benefit. Instead we build locally
    #   and import directly into K3S's own image store (K3S and Docker each keep their own separate copy of images).
    #   `pullPolicy: Never` in values.yaml tells K3S to only use the locally imported image — never try to pull it from the internet.
    #
    # WHY Docker layer cache is safe here (--no-cache is NOT used):
    #   Docker's build cache skips any steps in the Dockerfile that haven't changed.
    #   The K3S side is handled separately: the BUILD_TAG always has a fresh timestamp, so K3S
    #   always treats it as a new image and imports it fresh. The image cleanup steps below also
    #   remove any leftover old images from K3S. --no-cache was targeting Docker's cache (which
    #   was fine to keep), not K3S's cache — so it added 2-5 min to every deploy with no benefit.
    #
    # WHY Dockerfile changes ARE picked up by the cache:
    #   If you change the Dockerfile (like updating a pip package version), Docker detects it and
    #   rebuilds from that point forward. Everything before the change is reused.
    #   DAG files are not included in the Docker build — they're copied separately via rsync — so
    #   editing a DAG file does not trigger a Docker rebuild (which is correct: the image itself doesn't need to change).
    #
    # By using a new tag each time, K3S has never seen it before and always loads the image fresh.
    # (If you re-import under the same tag name, K3S can silently reuse the old cached version
    # even after you've deleted and re-imported it.)
    echo "Build tag: $BUILD_TAG"

    # Verify Docker daemon is still reachable right before building — Step 1c3 confirmed it
    # earlier, but the daemon can go down between then and now (e.g., OOM-killed on a small
    # instance running K3s + Docker + MariaDB simultaneously). timeout 10 prevents a wedged
    # daemon from hanging the build step indefinitely.
    if ! ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1"; then
        echo "Docker daemon not reachable at build time — restarting..."
        ssh "$EC2_HOST" "sudo systemctl restart docker"
        sleep 5
        if ! ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1"; then
            echo "✗ Docker daemon still not reachable after restart"
            echo "  Run 'sudo journalctl -u docker --no-pager -n 30' on EC2 to see why"
            return 1
        fi
        echo "✓ Docker daemon recovered"
    fi

    # Prune OLD images from Docker BEFORE building — this way the new image can never be accidentally deleted.
    # Previously, pruning ran after the build using grep -v to exclude the new tag, but that filter was
    # unreliable and ended up deleting the newly built image, causing the K3S import to fail.
    ssh "$EC2_HOST" "
        echo 'Pruning old airflow-dbt Docker images before build to free disk space...' ;
        docker images --format '{{.Repository}}:{{.Tag}}' | grep 'airflow-dbt' | xargs -r docker rmi 2>/dev/null || true ;
        echo 'Pruning dangling Docker images to free disk space...' ;
        for _p in 1 2 3 4 5; do
            out=\$(docker image prune -f 2>&1) && echo \"\$out\" && break ;
            echo \"\$out\" | grep -q 'prune operation is already running' \
                && echo \"Prune already running (attempt \$_p/5) — waiting 10s...\" && sleep 10 \
                || { echo \"\$out\"; break; }
        done || true
    "

    # Build the new image — retry once on failure to absorb transient Docker daemon hiccups.
    # The build now runs serialized (not in parallel with Kafka/MLflow), so SSH-drop-from-host-starvation
    # is no longer the expected failure mode; one extra attempt is enough for routine flakes.
    for _build_attempt in 1 2; do
        if ssh "$EC2_HOST" "
            echo 'Building airflow-dbt:$BUILD_TAG image (attempt $_build_attempt/2)...' &&
            DOCKER_BUILDKIT=1 docker build -t airflow-dbt:$BUILD_TAG $EC2_HOME/airflow/docker/
        "; then
            break  # build succeeded — exit the retry loop
        fi
        if [ "$_build_attempt" -lt 2 ]; then
            echo "Docker build attempt $_build_attempt failed — restarting Docker daemon and retrying in 20s..."
            ssh "$EC2_HOST" "sudo systemctl restart docker" || true  # restart to reset BuildKit connection
            sleep 20
        else
            echo "✗ Docker build failed after 2 attempts"
            return 1
        fi
    done

    # Purge stale K3S containerd snapshots AFTER the build so no old version is reused
    ssh "$EC2_HOST" "
        echo 'Purging ALL existing airflow-dbt images from K3S containerd (prevents stale snapshot reuse)...' ;
        sudo k3s ctr images ls | grep 'airflow-dbt' | awk '{print \$1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true
    "

    # Import the freshly built image into K3S — shared helper in common.sh handles save+import+verify
    import_image_to_k3s "airflow-dbt:$BUILD_TAG" "airflow-dbt"

    # GC orphaned content blobs left behind by previous imports.
    # k3s ctr images rm (above) only removes the tag reference; the actual layer bytes stay in the content
    # store as unreferenced blobs until a garbage collection runs. Without this, each deploy accumulates
    # ~1.2 GB of orphaned layers — 6+ deploys = 6+ GB of wasted disk, which is what triggers the 90% warning.
    ssh "$EC2_HOST" "sudo k3s ctr content gc && echo 'Containerd GC complete — orphaned blobs reclaimed'" || true

    # Throw away Docker's old copy of the image now that K3S has its own — recovers ~1 GB per build.
    # --filter 'until=1h' keeps the image we just built; -a removes all older unused images and their layer files.
    ssh "$EC2_HOST" "echo 'Pruning Docker image layer cache after K3S import...' && docker image prune -af --filter 'until=1h' 2>&1 | tail -5" || true
    # Clear Docker's build scratch pad (BuildKit cache) — it rebuilds on the next build if needed, no data is lost.
    # This is separate from image layers and can hold another 1-2 GB of intermediate build files from prior runs.
    ssh "$EC2_HOST" "echo 'Pruning Docker BuildKit cache after K3S import...' && docker builder prune -af --filter 'until=1h' 2>&1 | tail -5" || true
}
