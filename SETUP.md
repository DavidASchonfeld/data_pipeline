# Setup and Operations Guide

This document covers local development setup, production deployment, and how code flows from your Mac to running pods. For the project summary, architecture, and tech stack, see [README.md](README.md). For the deploy script details, see [docs/DEPLOY.md](docs/DEPLOY.md). For monthly costs, see [docs/COSTS.md](docs/COSTS.md).

---

## How Code Gets From Your Mac to Running Pods

Your code exists in three places at the same time:

### Place 1: Your Mac (the source of truth)

When you edit `dag_stocks.py` in VS Code, you're editing the file here:
```
Your Mac
  └── Documents/Programming/Python/Data-Pipeline-2026/data_pipeline/
        ├── airflow/dags/          ← Pipeline code (dag_stocks.py, edgar_client.py, etc.)
        ├── airflow/manifests/     ← Kubernetes config files (YAML)
        ├── dashboard/             ← Flask website code
        └── scripts/deploy.sh     ← The script that copies everything to EC2
```

If you want to change something, change it here first, then deploy.

### Place 2: The EC2 Server (the copy on AWS)

When you run `./scripts/deploy.sh`, it copies your files from your Mac to EC2:
```
EC2 (Amazon cloud server)
  └── /home/ubuntu/
        ├── airflow/dags/          ← Copy of your pipeline code
        ├── airflow/manifests/     ← Copy of your Kubernetes configs
        └── dashboard/             ← Copy of your Flask code
```

You connect to EC2 via SSH — think of it like making a phone call to the server:
```bash
ssh ec2-stock
```

### Place 3: Inside the Pods (mounted from EC2)

Kubernetes (K3S) creates pods — tiny isolated computers running inside EC2. Your DAG files are not copied again; Kubernetes uses a "mount" (like plugging in a USB drive) to make the EC2 folder visible inside the pod:

```
EC2 server
  └── /home/ubuntu/airflow/dags/dag_stocks.py    ← File on EC2's hard drive
        │
        │ (Kubernetes mounts this folder into the pod)
        ▼
  Airflow Pod
    └── /opt/airflow/dags/dag_stocks.py           ← Same file, seen from inside the pod
```

### The Mount System (PV/PVC)

Pods don't have their own hard drive. They "borrow" folders from EC2 using PersistentVolumes (PVs) and PersistentVolumeClaims (PVCs):

- A **PV** says: "There is a folder on EC2 at `/home/ubuntu/airflow/dags/`"
- A **PVC** says: "I need access to that folder"
- The pod mounts the PVC at `/opt/airflow/dags/` inside itself

Think of it like: a PV is a filing cabinet in room 204, a PVC is a request to access it, and the mount is a door from the pod directly to that cabinet.

### The Full Journey

```
1. You edit dag_stocks.py on your Mac
2. You run ./scripts/deploy.sh
3. deploy.sh copies the file to EC2 via rsync (smart copy — only sends changes)
4. Kubernetes makes that file visible inside the Airflow pod via the PV mount
5. Airflow reads the file and runs your pipeline
```

---

## Local Development (Mac)

No Docker or Kubernetes needed. Run MariaDB natively, point the code at `localhost`, and run Flask and Airflow directly in a venv.

### 1. Install MariaDB
```bash
brew install mariadb
brew services start mariadb
sudo mysql -u root   # Homebrew uses unix_socket auth — needs sudo
```
> `mysql -u root` (without `sudo`) returns `Access denied` on Mac because Homebrew authenticates root via the OS user. After creating `airflow_user` below, all connections use that user with a password.

```sql
CREATE DATABASE database_one;
CREATE USER 'airflow_user'@'localhost' IDENTIFIED BY 'YOUR_DB_PASSWORD';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 2. Create secret files (never commit these)

**`.env`** — all local secrets *(at `data_pipeline/.env`)*:
```bash
DB_USER=airflow_user
DB_PASSWORD=YOUR_DB_PASSWORD
DB_NAME=database_one
DB_HOST=localhost
EDGAR_CONTACT_EMAIL=your.email@gmail.com
```

**`airflow/dags/db_config.py`**:
```python
DB_USER     = "airflow_user"
DB_PASSWORD = "YOUR_DB_PASSWORD"
DB_NAME     = "database_one"
DB_HOST     = "localhost"
```

### 3. Update the logs path
```python
# airflow/dags/constants.py
outputTextsFolder_folderPath = "/Users/<you>/path/to/data_pipeline/logs"
```

### 4. Run the Flask dashboard
```bash
cd dashboard
pip install -r requirements.txt
python app.py
# Visit http://localhost:5000/dashboard/
```

### 5. Run Airflow
```bash
python -m venv airflow_env && source airflow_env/bin/activate
pip install apache-airflow pandas sqlalchemy pymysql requests
export AIRFLOW_HOME=$(pwd)
airflow db migrate
airflow users create --username admin --password admin \
  --firstname Air --lastname Flow --role Admin --email admin@example.com
airflow webserver &        # http://localhost:8080
airflow scheduler
```

---

## Production Deployment (EC2 + K3S)

> Real values for IPs and credentials are in `infra_local.md` (gitignored).

### One-time infrastructure setup
1. Launch EC2 t3.large, Ubuntu 24.04 LTS, 100 GiB gp3, assign Elastic IP
2. Open inbound ports: 22, 30080 (Airflow UI), 32147 (Flask)
3. Install K3S: `curl -sfL https://get.k3s.io | sh -`
4. Install Helm, add Airflow repo: `helm repo add apache-airflow https://airflow.apache.org`

### First-time: create `.env.deploy`
```bash
cp .env.deploy.example .env.deploy
# Fill in: ECR_REGISTRY="<AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com", AWS_REGION
```

### Deploy updates
```bash
./scripts/deploy.sh
```

See [docs/DEPLOY.md](docs/DEPLOY.md) for the full deploy guide, including all modes, prerequisites, and troubleshooting.

### Accessing the UIs

**SSH tunnel (recommended)** — keeps AWS ports closed; traffic goes through your SSH connection instead.

**How to use it:**
1. Open a Terminal window and run the command below. It will log you into the EC2 server and hold the connection open — **leave this Terminal window open the entire time you want to access the UIs.**
2. While the tunnel is running, open your web browser and visit the URLs listed below — they work just like any local website.
3. When you're done, close the Terminal window (or press `Ctrl+C`) to shut the tunnel down.

```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 -L 5500:localhost:5500 ec2-stock
```

> If the terminal window is closed or the connection drops, the browser URLs will stop working. Just re-run the command above to reconnect.

| UI | URL |
|---|---|
| Airflow | http://localhost:30080 |
| Dashboard | http://localhost:32147/dashboard/ |
| MLflow | http://localhost:5500 |

**Public access (for demos):** Open port 32147 in the EC2 Security Group inbound rules. Remove the rule after the demo.

---

## Kubernetes Pods and Namespaces

Pods are organized into namespaces — think of them as different floors of an office building:

| Pod | What it does | Namespace |
|-----|-------------|-----------|
| `airflow-scheduler-0` | Runs DAG pipelines on schedule | `airflow-my-namespace` |
| `airflow-dag-processor-...` | Reads and parses DAG files | `airflow-my-namespace` |
| `airflow-api-server-...` | Serves the Airflow web UI | `airflow-my-namespace` |
| `airflow-triggerer-0` | Handles delayed/deferred tasks | `airflow-my-namespace` |
| `airflow-postgresql-0` | Airflow's internal database (not your data) | `airflow-my-namespace` |
| `mlflow-...` | MLflow experiment tracking server | `airflow-my-namespace` |
| `kafka-0` | Kafka message broker | `kafka` |
| `my-kuber-pod-flask` | Flask website/dashboard | `default` |

### How to look at pods

```bash
# See all pods on all namespaces
ssh ec2-stock kubectl get pods --all-namespaces

# See just the Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# See the Flask pod
ssh ec2-stock kubectl get pods -n default
```

### How to run commands inside a pod

```bash
# Run a command inside the Airflow scheduler pod
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls /opt/airflow/dags/
```

Breaking that down:
- `ssh ec2-stock` — connect to EC2
- `kubectl exec` — go inside a pod and run a command
- `-n airflow-my-namespace` — which namespace
- `airflow-scheduler-0` — which pod
- `--` — everything after this is the command to run inside the pod

### Where am I right now?

| Where you are | How you got there | Your prompt looks like | How to leave |
|---------------|-------------------|----------------------|-------------|
| Your Mac | Default — you opened Terminal | `David@Davids-MacBook ~ %` | (you're already here) |
| EC2 server | Ran `ssh ec2-stock` | `[ubuntu@ip-... ~]$` | Type `exit` |
| Inside a pod | Ran `kubectl exec -it ... -- bash` | `airflow@airflow-scheduler-0:/$` | Type `exit` |

The most common mistake is forgetting which "level" you're on. If you're inside EC2 and try to edit a local file, it won't work. If you're on your Mac and try to run `kubectl` without the SSH prefix, it won't find the cluster.

### kubectl context
The kubectl context on EC2 defaults to `airflow-my-namespace`. Always specify `-n default` for Flask resources:
```bash
kubectl get pods                      # shows Airflow pods only
kubectl get pods -n default           # shows the Flask pod
kubectl get pods --all-namespaces     # shows everything
```

---

## Project Structure

```
data_pipeline/
├── airflow/
│   ├── dags/                   DAG files + support modules (mounted into pods via PVC)
│   ├── helm/values.yaml        Active Helm values for Airflow deployment
│   └── manifests/              K8s PV/PVC/Service YAML files
├── dashboard/
│   ├── app.py                  Flask + Dash entry point
│   ├── db.py, charts.py, routes.py, callbacks.py
│   ├── Dockerfile              Builds my-flask-app:latest
│   └── manifests/              Pod and Service YAML
├── kafka/                      Kafka StatefulSet and setup notes
├── terraform/                  Infrastructure-as-Code (AWS resources)
├── docs/                       Full documentation (see docs/INDEX.md)
├── scripts/deploy.sh           One-command deploy script (see docs/DEPLOY.md)
├── .env.deploy.example         Template for AWS deploy secrets
└── README.md                   Project summary and architecture
```

For the full architecture and tech stack, see [README.md](README.md).
