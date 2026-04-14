#!/bin/bash
# Module: bootstrap — auto-installs base software on a fresh spot instance.
# Sourced by deploy.sh; all variables from common.sh are available here.
# Only runs when --provision is used and K3s is not found on EC2.

step_auto_bootstrap() {
    echo ""
    echo "============================================================"
    echo "  Fresh spot instance detected — running auto-bootstrap"
    echo "============================================================"
    echo ""
    echo "K3s is not installed on this instance. This happens when the"
    echo "ASG launched a brand-new spot instance. Installing required"
    echo "software automatically (K3s, Docker, Helm, MariaDB, AWS CLI)."
    echo ""

    # Validate that .env.deploy has the extra credentials needed for a fresh instance
    for var in DB_PASSWORD SEC_EDGAR_EMAIL; do
        if [ -z "${!var:-}" ]; then
            echo "ERROR: $var is not set in .env.deploy."
            echo "  Auto-bootstrap needs DB_PASSWORD and SEC_EDGAR_EMAIL to create the"
            echo "  db-credentials K8s secret. Add them to .env.deploy and re-run."
            echo "  See .env.deploy.example for the template."
            exit 1
        fi
    done

    # ── Install base packages ─────────────────────────────────────────────────
    echo "=== Bootstrap: Installing base packages (MariaDB, curl, unzip) ==="
    ssh "$EC2_HOST" "sudo apt-get update -y && sudo apt-get install -y mariadb-server unzip curl ca-certificates gnupg"
    ssh "$EC2_HOST" "sudo systemctl enable --now mariadb"

    # ── Docker CE (official repo) ─────────────────────────────────────────────
    echo "=== Bootstrap: Installing Docker CE from official repo (includes buildx plugin) ==="
    # docker.io from Ubuntu apt repos lacks docker-buildx-plugin; DOCKER_BUILDKIT=1 hangs indefinitely without it
    ssh "$EC2_HOST" "sudo install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg && sudo chmod a+r /etc/apt/keyrings/docker.gpg"
    ssh "$EC2_HOST" '. /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null'
    ssh "$EC2_HOST" "sudo apt-get update -y && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin"
    ssh "$EC2_HOST" "sudo systemctl enable --now docker"
    # Add ubuntu to docker group so deploy.sh can run docker commands without sudo
    ssh "$EC2_HOST" "sudo usermod -aG docker ubuntu"

    # ── AWS CLI v2 ────────────────────────────────────────────────────────────
    echo "=== Bootstrap: Installing AWS CLI v2 ==="
    # apt install awscli gives CLI v1 (deprecated); use the official v2 installer
    ssh "$EC2_HOST" "
        ARCH=\$(dpkg --print-architecture)
        if [ \"\$ARCH\" = 'arm64' ]; then
            URL='https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip'
        else
            URL='https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip'
        fi
        curl -fsSL \"\$URL\" -o /tmp/awscliv2.zip \
            && unzip -qo /tmp/awscliv2.zip -d /tmp/awscliv2-install \
            && sudo /tmp/awscliv2-install/aws/install --update \
            && rm -rf /tmp/awscliv2.zip /tmp/awscliv2-install
    "

    # ── K3s ───────────────────────────────────────────────────────────────────
    echo "=== Bootstrap: Installing K3s ==="
    ssh "$EC2_HOST" "curl -sfL https://get.k3s.io | sh -"

    echo "=== Bootstrap: Configuring kubectl for ubuntu user ==="
    ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml \
        && mkdir -p ~/.kube \
        && cp /etc/rancher/k3s/k3s.yaml ~/.kube/config \
        && grep -qxF 'export KUBECONFIG=~/.kube/config' ~/.bashrc \
            || echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc"

    # ── Helm ──────────────────────────────────────────────────────────────────
    echo "=== Bootstrap: Installing Helm ==="
    ssh "$EC2_HOST" "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
    ssh "$EC2_HOST" "helm repo add apache-airflow https://airflow.apache.org && helm repo update"

    # ── Wait for K3s node to be Ready ─────────────────────────────────────────
    echo "=== Bootstrap: Waiting for K3s node to be Ready (up to 5 minutes) ==="
    ssh "$EC2_HOST" "
        for i in \$(seq 1 30); do
            if kubectl get nodes 2>/dev/null | grep -q ' Ready'; then
                echo 'K3s node is Ready'; kubectl get nodes; break
            fi
            echo \"Attempt \$i/30 — K3s not ready yet, waiting 10s...\"; sleep 10
            if [ \$i -eq 30 ]; then echo 'ERROR: K3s did not become Ready after 5 minutes'; exit 1; fi
        done
    "

    # ── Kubernetes namespace + PV/PVC ─────────────────────────────────────────
    echo "=== Bootstrap: Creating airflow-my-namespace ==="
    ssh "$EC2_HOST" "kubectl create namespace airflow-my-namespace --dry-run=client -o yaml | kubectl apply -f -"

    echo "=== Bootstrap: Creating host directories ==="
    ssh "$EC2_HOST" "sudo mkdir -p /opt/airflow/logs /opt/airflow/out \
        && sudo chown -R ubuntu:ubuntu /opt/airflow"

    echo "=== Bootstrap: Syncing and applying PV/PVC manifests ==="
    # PV/PVC manifests need to exist in K8s before Helm can install Airflow
    rsync $RSYNC_FLAGS "$PROJECT_ROOT/airflow/manifests/" "$EC2_HOST:$EC2_HOME/airflow/manifests/"
    for f in pv-dags.yaml pv-airflow-logs.yaml pv-output-logs.yaml; do
        ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/$f"
    done
    for f in pvc-dags.yaml pvc-airflow-logs.yaml pvc-output-logs.yaml; do
        ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/$f -n airflow-my-namespace"
    done

    # ── MariaDB setup ─────────────────────────────────────────────────────────
    echo "=== Bootstrap: Setting up MariaDB (fresh database) ==="
    ssh "$EC2_HOST" "sudo mysql -e 'CREATE DATABASE IF NOT EXISTS database_one;'"

    # Create airflow_user for both the K3s pod network (10.42.%) and the instance's own private IP
    ssh "$EC2_HOST" "
        NEW_IP=\$(hostname -I | awk '{print \$1}')
        echo \"Detected private IP: \$NEW_IP\"
        sudo mysql <<SQL
CREATE USER IF NOT EXISTS 'airflow_user'@'10.42.%' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS 'airflow_user'@'\${NEW_IP}' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'10.42.%';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'\${NEW_IP}';
FLUSH PRIVILEGES;
SQL
    "

    # Allow connections from K3s pods (default bind-address only allows localhost)
    ssh "$EC2_HOST" "sudo sed -i 's/^bind-address\s*=.*/bind-address = 0.0.0.0/' \
        /etc/mysql/mariadb.conf.d/50-server.cnf \
        && sudo systemctl restart mariadb"

    # ── db-credentials K8s secret ─────────────────────────────────────────────
    echo "=== Bootstrap: Creating db-credentials K8s secret ==="
    ssh "$EC2_HOST" "
        NEW_IP=\$(hostname -I | awk '{print \$1}')
        for NS in airflow-my-namespace default; do
            kubectl create secret generic db-credentials -n \$NS \
                --from-literal=DB_USER=airflow_user \
                --from-literal=DB_PASSWORD='${DB_PASSWORD}' \
                --from-literal=DB_HOST=\$NEW_IP \
                --from-literal=DB_NAME=database_one \
                --from-literal=SEC_EDGAR_EMAIL='${SEC_EDGAR_EMAIL}' \
                --from-literal=SLACK_WEBHOOK_URL='${SLACK_WEBHOOK_URL:-}' \
                --dry-run=client -o yaml | kubectl apply -n \$NS -f -
            echo \"db-credentials created in namespace: \$NS\"
        done
    "

    echo ""
    echo "============================================================"
    echo "  Auto-bootstrap complete — continuing with deploy"
    echo "============================================================"
    echo ""
}
