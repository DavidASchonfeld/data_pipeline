# Backlog & TODOs

Actionable steps ordered by dependency. Complete Step 2 items before Step 3 items — Kafka benefits from the RAM freed by removing MariaDB.

---

## Status Key

| Symbol | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Done |

---

## Immediate — Before / During t3.large Launch

> t3.large is safe to launch now. No changes required to the running stack before switching instance types.

- [x] Launch new EC2 instance from copied AMI using type **t3.large** (Runbook #13, Phase C Step 6)
- [x] Allocate new Elastic IP in us-east-1 (`52.70.211.1`) and associate it with the new instance (Runbook #13, Phase C Step 7)
- [x] Update `.env.deploy`: `ECR_REGISTRY` and `AWS_REGION` now point to us-east-1
- [x] Added K8s resource limits to Flask pod (`pod-flask.yaml`) and all Airflow components (`values.yaml`) — t3.large safe; see EC2_SIZING.md for rationale
- [x] Update `~/.ssh/config` on your Mac: set `HostName 52.70.211.1` under `Host ec2-stock` (see Runbook #13 Phase E)
- [ ] **Attach IAM role to new EC2 instance** — AMIs do NOT carry the IAM role over. Without it, `./scripts/deploy.sh` fails at Step 4 ("Unable to locate credentials") because the instance can't get a token to push to ECR. Fix: EC2 Console → select instance → **Actions → Security → Modify IAM role** → attach the same role the old instance used. Verify: `ssh ec2-stock 'aws sts get-caller-identity'`
- [~] Run `./scripts/deploy.sh` — blocked until IAM role is attached
- [ ] Verify all pods are healthy after deploy: `ssh ec2-stock kubectl get pods --all-namespaces`
- [ ] Verify resource limits active: `kubectl describe pod my-kuber-pod-flask -n default | grep -A6 "Limits:"`
- [ ] Check RAM headroom after go-live: `ssh ec2-stock free -h` — should show ~4–5GB free with current stack

---

## Step 2 — Snowflake Migration (do this before adding Kafka)

Snowflake replaces MariaDB. Moving the database to the cloud removes ~300–500MB RAM from EC2 and is the single biggest action that gives t3.large comfortable long-term headroom.

### 2a. Snowflake setup
- [ ] Create Snowflake account (free trial at snowflake.com)
- [ ] Create warehouse, database, schema, and tables matching current MariaDB schema (`company_financials`, `weather_hourly`)
- [ ] Add Snowflake credentials to `.env` file locally and to K8s Secret on EC2

### 2b. Code migration
- [ ] Replace `db_config.py` + `pymysql`/`SQLAlchemy` with `snowflake-sqlalchemy` (or `snowflake-connector-python`)
- [ ] Update `load()` tasks in `dag_stocks.py` and `dag_weather.py` to write to Snowflake
- [ ] Update `dashboard/app.py` to query Snowflake instead of MariaDB
- [ ] Update `dashboard/requirements.txt` with Snowflake connector package
- [ ] Test DAG runs end-to-end: data flows from SEC EDGAR / Open-Meteo → Snowflake → dashboard

### 2c. Remove MariaDB from EC2 (after data confirmed in Snowflake)
- [ ] Stop and disable MariaDB on EC2:
  ```bash
  sudo systemctl stop mariadb
  sudo systemctl disable mariadb
  ```
- [ ] Optionally uninstall: `sudo yum remove mariadb-server` (or `apt remove` if using Ubuntu)
- [ ] Delete MariaDB-related K8s manifests if any exist (check `airflow/manifests/`)
- [ ] Confirm ~300–500MB freed: `ssh ec2-stock free -h`

---

## Step 3 — Kafka Setup (t3.large-specific tuning required)

Do this after Step 2. With MariaDB gone, you have the headroom to run Kafka safely on t3.large.

### 3a. Kafka K8s manifest
- [ ] Write Kafka deployment manifest using **KRaft mode** (no Zookeeper — saves ~500MB RAM)
  - Required env vars: `KAFKA_PROCESS_ROLES=broker,controller`, `KAFKA_NODE_ID=1`, `KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093`
- [ ] Set Kafka JVM heap to 768MB to stay within t3.large budget:
  ```yaml
  - name: KAFKA_HEAP_OPTS
    value: "-Xmx768m -Xms768m"
  ```
- [ ] Set K8s resource limits on Kafka pod: `memory: 1Gi` limit, `900Mi` request
- [ ] Add `KAFKA_HEAP_OPTS` as a documented placeholder in `.env.deploy.example`

### 3b. DAG integration
- [ ] Add `confluent-kafka-python` (or `kafka-python`) to Airflow pod requirements
- [ ] Modify `extract()` tasks in DAGs to produce messages to a Kafka topic instead of calling `transform()` directly
- [ ] Write Kafka consumer (or configure Kafka Connect Snowflake Sink) to write from topic → Snowflake

### 3c. Verification
- [ ] Confirm Kafka pod is Running: `kubectl get pods -A`
- [ ] Confirm no OOMKilled pods after Kafka starts (`OOMKilled` = Out Of Memory Killed — pod was force-killed for exceeding its RAM limit; shows up as high RESTARTS): `kubectl get pods -A` — check RESTARTS column
- [ ] Run `ssh ec2-stock free -h` — confirm at least 1.5GB free RAM remains under load
- [ ] Trigger a DAG run and confirm data flows all the way: Kafka topic → Snowflake → dashboard

---

## dbt (Step 2b — after Snowflake is working)

### What Is dbt, and Why Use It?

Right now the pipeline does extraction, transformation, and loading all inside the DAG tasks using Python and Pandas. This works for a few tables, but as the number of tables grows, transformation logic gets buried in Python functions spread across DAG files.

dbt (data build tool) moves all transformation logic into **SQL files** in version control — one file per table, with a clear name and automated tests. The Airflow DAG handles "get the data in." dbt handles "make the data useful."

Each dbt "model" is a `.sql` file:
```sql
-- Annual revenue for each company — cleaned and filtered
SELECT ticker, entity_name, period_end, fiscal_year, value AS revenue_usd, filed_date
FROM {{ ref('stg_company_financials') }}
WHERE metric = 'Revenues' AND fiscal_period = 'FY'
ORDER BY ticker, period_end
```

The `{{ ref(...) }}` is dbt's dependency system — it knows to run the staging model first, then this one, building a lineage graph automatically. dbt also supports built-in data quality tests (not-null, accepted values) defined in YAML alongside the models.

`astronomer-cosmos` integrates dbt with Airflow by turning each dbt model into its own Airflow task with separate logs.

### Tasks

- [x] Install `dbt-snowflake` and initialize a dbt project inside the repo
- [x] Move `transform()` logic out of DAGs into dbt models (SQL files)
- [x] Change DAG flow to: `extract()` → `load_raw()` → `dbt run` (via BashOperator or `astronomer-cosmos`)
- [x] Add dbt tests for data quality (not-null, accepted values, etc.)

---

## Ongoing Monitoring (post t3.large launch)

- [ ] After all Step 2 and Step 3 work: run the [EC2 RAM monitoring commands](infrastructure/EC2_SIZING.md#monitoring-ram-after-go-live) to confirm baseline
- [ ] Watch for OOMKilled pods in the first week (`OOMKilled` = pod killed by OS for exceeding RAM limit): `kubectl get pods -A` after each DAG run
- [ ] If RAM usage consistently above 85% or any OOMKilled appears → resize to t3.xlarge (AWS Console: stop instance → change type → start)

---

**Last updated:** 2026-04-04 — EIP wired up (52.70.211.1), .env.deploy updated to us-east-1, K8s resource limits added.
