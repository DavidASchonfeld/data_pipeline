#!/bin/bash
# Module: setup — EC2 directory prep, kubectl permissions, and pre-flight DAG validation.
# Sourced by deploy.sh; all variables from common.sh are available here.

step_setup() {
    # Wait for SSH before the first remote command — terraform apply can leave sshd briefly unreachable.
    _wait_ssh_ready
    echo "=== Step 1: Ensuring target directories exist on EC2 ==="
    ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests $EC2_HOME/airflow/dag-mylogs $EC2_HOME/airflow/docker $EC2_HOME/kafka/k8s \
        && chmod 777 $EC2_HOME/airflow/dag-mylogs"  # 777 gives the Airflow pod (which runs as user 50000) permission to write logs to this folder

    echo "=== Step 1c: Ensuring kubectl config is accessible ==="
    # K3s stores its cluster config at /etc/rancher/k3s/k3s.yaml (not ~/.kube/config like a normal kubectl install).
    # K3s creates this file as root-only by default, so we open it up (chmod 644) so the ubuntu user can read it.
    # We do this on every deploy because K3s resets the file permissions when it restarts.
    # On a fresh spot instance, K3s is not installed yet — auto-bootstrap handles it if --provision was used.
    if ! ssh "$EC2_HOST" "test -f /etc/rancher/k3s/k3s.yaml"; then
        if [ "${PROVISION:-false}" = true ]; then
            # --provision was used, so we know Terraform just launched this instance — install everything automatically
            step_auto_bootstrap
        else
            echo ""
            echo "ERROR: /etc/rancher/k3s/k3s.yaml not found on EC2."
            echo "  K3s is not installed on this instance. This happens when the ASG"
            echo "  launched a fresh spot instance that has not been set up yet."
            echo ""
            echo "  Re-run with --provision to auto-bootstrap the instance:"
            echo "    ./scripts/deploy.sh --provision"
            echo ""
            echo "  Or run the bootstrap script manually:"
            echo "    ./scripts/bootstrap_ec2.sh <ssh-host>"
            echo ""
            echo "  Then re-run this deploy."
            exit 1
        fi
    fi
    ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml"

    # Verify Docker has the BuildKit buildx plugin — required by DOCKER_BUILDKIT=1 in the Airflow image build.
    # Instances bootstrapped before the docker.io→Docker CE fix (or via the old bootstrap_ec2.sh) may have
    # Docker installed from Ubuntu's docker.io package, which does not include docker-buildx-plugin.
    # Without this check, the build hangs for 8+ minutes before printing a cryptic error.
    echo "=== Step 1c2: Verifying Docker BuildKit (buildx plugin) ==="
    if ! ssh "$EC2_HOST" "docker buildx version >/dev/null 2>&1"; then
        echo ""
        echo "Docker buildx plugin not found — upgrading from docker.io to Docker CE..."
        echo "(Ubuntu's docker.io package does not include buildx; Docker CE does.)"
        echo ""
        # Remove docker.io first to avoid package conflicts
        ssh "$EC2_HOST" "sudo apt-get remove -y docker.io docker-doc docker-compose podman-docker containerd runc 2>/dev/null || true"
        # Add Docker's official GPG key and apt repository
        ssh "$EC2_HOST" "sudo install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg && sudo chmod a+r /etc/apt/keyrings/docker.gpg"
        ssh "$EC2_HOST" '. /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null'
        ssh "$EC2_HOST" "sudo apt-get update -y && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin"
        ssh "$EC2_HOST" "sudo systemctl enable --now docker && sudo usermod -aG docker ubuntu"
        # Verify the upgrade worked
        if ssh "$EC2_HOST" "docker buildx version >/dev/null 2>&1"; then
            echo "✓ Docker CE with buildx plugin installed successfully"
        else
            echo "✗ Docker buildx still not available after upgrade — check logs"
            exit 1
        fi
    else
        echo "✓ Docker buildx plugin is available"
    fi

    # Verify the Docker daemon is running — 'docker buildx version' above only checks the client-side
    # plugin binary; it does NOT contact the daemon. Without this check, the deploy passes all pre-flight
    # steps and then fails minutes later at the Docker build with "Cannot connect to the Docker daemon".
    echo "=== Step 1c3: Verifying Docker daemon is running ==="
    # timeout 10 prevents hanging indefinitely if the daemon is wedged (accepts connections but never responds)
    if ! ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1"; then
        echo "Docker daemon is not running — attempting to start it..."
        ssh "$EC2_HOST" "sudo systemctl start docker"
        # Give the daemon a moment to initialize before checking again
        sleep 3
        if ssh "$EC2_HOST" "timeout 10 docker info >/dev/null 2>&1"; then
            echo "✓ Docker daemon started successfully"
        else
            echo "✗ Docker daemon failed to start — check 'sudo systemctl status docker' on EC2"
            exit 1
        fi
    else
        echo "✓ Docker daemon is running"
    fi

    echo "=== Step 1b: Pre-flight validation ==="

    # Validate Python syntax in all DAG files (catches typos, indentation errors, missing colons)
    # We check the exit code (not the output text) because py_compile signals errors by exiting with a non-zero code, which is more reliable than grepping the output
    echo "Checking Python syntax in DAG files..."
    if find "$PROJECT_ROOT/airflow/dags" -name "*.py" | xargs python3 -m py_compile 2>/dev/null; then
        echo "✓ All DAG files have valid Python syntax"
    else
        echo "✗ Syntax error in DAG files. Fix before deploying."
        find "$PROJECT_ROOT/airflow/dags" -name "*.py" | xargs python3 -m py_compile  # run again without silencing output, so the error message is visible
        exit 1
    fi

    # Validate that all DAG imports work (catches missing modules, missing secrets, etc.)
    # The parentheses ( ) create a subshell so the cd doesn't affect the rest of the script
    echo "Validating module imports..."
    (
        cd "$PROJECT_ROOT/airflow/dags"
        python3 << 'VALIDATION_EOF'
import sys
sys.path.insert(0, '.')  # Add the current folder to the Python path, which is what the Airflow pod does at /opt/airflow/dags

# Skip import check if airflow is not installed locally (only available inside the pod)
try:
    import airflow
except ImportError:
    print("⚠ airflow not installed locally — skipping import validation (syntax already verified above)")
    sys.exit(0)

# Try importing all DAG files
dag_files = ['dag_stocks', 'dag_weather', 'dag_staleness_check', 'dag_stocks_consumer', 'dag_weather_consumer']
for dag_file in dag_files:
    try:
        __import__(dag_file)
        print(f"✓ {dag_file} imports successfully")
    except ImportError as e:
        print(f"✗ Import error in {dag_file}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Runtime error in {dag_file}: {e}")
        sys.exit(1)

print("✓ All DAG files import successfully")
VALIDATION_EOF
    )

    echo ""
}
