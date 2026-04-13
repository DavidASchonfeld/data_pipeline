# Verification Checklist

Run these steps in order after deploying the project (via `./scripts/deploy.sh`). Each section has a clear **Pass** condition. If a step fails, fix it before continuing — later steps depend on earlier ones.

For the full deploy process, see [DEPLOY.md](DEPLOY.md).

---

## How to Run These Tests

All verification steps run on the EC2 instance. You can access it in three ways:

**Option 1: Run commands from your Mac (no SSH session needed)**
```bash
ssh <your-instance> "kubectl get pods -n airflow-my-namespace"
```

**Option 2: Open an interactive SSH session**
```bash
ssh <your-instance>
kubectl get pods -n airflow-my-namespace
```

**Option 3: Port forwarding (for browser-based UIs)**
```bash
ssh <your-instance> -L 5500:localhost:5500 -L 32147:localhost:32147
# Then open http://localhost:5500 (MLflow) or http://localhost:32147/dashboard (Dashboard)
```

> Replace `<your-instance>` with your EC2 SSH alias or address (e.g., `ubuntu@192.0.2.100` or `ec2-stock` from `~/.ssh/config`).

---

## Step 1 — All Pods Running

```bash
kubectl get pods -n airflow-my-namespace
kubectl get pods -n kafka
kubectl get pods -n default
```

**Pass:** Every pod shows `Running` or `Completed`. No `CrashLoopBackOff`, `Pending`, or `ImagePullBackOff`.

Expected pods:
- `airflow-my-namespace`: scheduler, api-server, triggerer, dag-processor, postgresql, mlflow
- `kafka`: kafka-0 with `1/1` READY
- `default`: my-kuber-pod-flask

---

## Step 2 — PersistentVolumes Mounted

```bash
kubectl get pvc -n airflow-my-namespace
kubectl get pvc -n kafka
```

**Pass:** All PVCs show `Bound`. If any are `Pending`, wait 60s and retry.

---

## Step 3 — Snowflake Connection

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow connections get snowflake_default
```

**Pass:** Prints connection details with a non-empty `account` field in `extra_dejson` (e.g., `"account": "qztxwkd-lsc26305"`). SnowflakeHook 6.x reads the account from `extra.account`, not the `host` field — if `account` is blank, re-run `./scripts/deploy.sh`.

Confirm target schemas exist (run in a Snowflake worksheet):
```sql
SHOW SCHEMAS IN DATABASE PIPELINE_DB;
```

**Pass:** Four schemas visible: `RAW`, `STAGING`, `MARTS`, `ANALYTICS`.

---

## Step 4 — Kafka Topics Exist

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092
```

**Pass:** Both topics present: `stocks-financials-raw` and `weather-hourly-raw`.

If topics are missing, create them manually:
```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --create --topic stocks-financials-raw \
    --bootstrap-server localhost:9092 --partitions 1 --replication-factor 1 \
    --config retention.ms=172800000 --config retention.bytes=104857600

kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --create --topic weather-hourly-raw \
    --bootstrap-server localhost:9092 --partitions 1 --replication-factor 1 \
    --config retention.ms=172800000 --config retention.bytes=104857600
```

---

## Step 5 — Airflow Variables Set

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables list
```

**Pass:** At minimum: `MLFLOW_TRACKING_URI`, `VACATION_MODE`, `SF_STOCKS_LAST_WRITE_DATE`.

---

## Step 6 — All 5 DAGs Parse Without Errors

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list
```

**Pass:** All five DAGs appear: `Stock_Market_Pipeline`, `stock_consumer_pipeline`, `API_Weather-Pull_Data`, `weather_consumer_pipeline`, `Data_Staleness_Monitor`.

> Duplicate rows are normal in Airflow 3.x — each processor worker registers the DAG separately.

Check for import errors:
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace | grep -i "import error\|broken dag" | tail -20
```

**Pass:** No import error lines.

### Step 6a — ml-venv Exists in Scheduler Pod

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/pip list | grep -E "scikit-learn|mlflow|snowflake"
```

**Pass:** All three packages appear with version numbers.

### Step 6b — All Consumer Tasks Registered

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks list stock_consumer_pipeline
```

**Pass:** All six tasks present: `check_new_rows`, `consume_from_kafka`, `dbt_run`, `dbt_test`, `detect_anomalies`, `write_to_snowflake`.

---

## Step 7 — dbt Is Functional

Verify dbt venv:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "/opt/dbt-venv/bin/dbt --version 2>&1"
```

**Pass:** Prints dbt version (1.8.x).

> dbt 1.8.x writes all output to stderr. The `bash -c "... 2>&1"` pattern merges streams so output reaches the terminal.

Confirm dbt-profiles secret is mounted:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "ls /dbt/profiles.yml 2>&1"
```

**Pass:** Prints `/dbt/profiles.yml`.

Run compile check:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "mkdir -p /tmp/dbt_target /tmp/dbt_logs && \
    DBT_PROFILES_DIR=/dbt \
    DBT_TARGET_PATH=/tmp/dbt_target \
    DBT_LOG_PATH=/tmp/dbt_logs \
    /opt/dbt-venv/bin/dbt --debug compile \
    --project-dir /opt/airflow/dags/dbt \
    --select tag:stocks \
    --no-use-colors 2>&1"
```

**Pass:** Exits 0 with `Command 'dbt compile' succeeded`.

### Step 7a — Dry-Run Anomaly Detector

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

**Pass:**
- No WARNING lines in output
- Last line is valid JSON, e.g.: `{"n_anomalies": 1, "n_total": 16, "mlflow_run_id": "..."}`
- No Python tracebacks

---

## Step 8 — End-to-End: Stocks Pipeline

**Before triggering, do two things:**

1. Reset Kafka consumer group offset (required on fresh deploy or after any Kafka restart):
```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group stocks-consumer-group \
    --reset-offsets --to-latest \
    --topic stocks-financials-raw --execute
```

2. Reset the daily batch gate (prevents the pipeline from skipping if it already ran today):
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set SF_STOCKS_LAST_WRITE_DATE ""
```

**Trigger the pipeline:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger Stock_Market_Pipeline
```

**Poll until complete** (~5–10 min):
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs Stock_Market_Pipeline
```

**Pass:** `state = success`.

**Check consumer was auto-triggered:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs stock_consumer_pipeline
```

**Pass:** `state = success`.

**Verify all 6 consumer tasks ran:**
```bash
# Replace <run_id> with the run_id from list-runs above
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks states-for-dag-run stock_consumer_pipeline <run_id>
```

**Pass:** `consume_from_kafka`, `write_to_snowflake`, `check_new_rows`, `dbt_run`, `dbt_test`, `detect_anomalies` all show `success`.

> **If dbt_run / dbt_test / detect_anomalies show `skipped`:** The `check_new_rows` gate fired (0 new rows written). Common causes: (1) Kafka offset not reset before triggering, or (2) Snowflake connection failed. Check logs at `/opt/airflow/out/`.

> **About the daily batch gate:** The pipeline writes to Snowflake once per day. If data was already written today, `check_new_rows` skips downstream tasks. This is intentional cost-saving behavior, not a bug. To force a full run, reset `SF_STOCKS_LAST_WRITE_DATE` as shown above.

---

## Step 9 — Verify Snowflake Data (Stocks)

Run in a Snowflake worksheet:
```sql
SELECT COUNT(*) FROM PIPELINE_DB.RAW.COMPANY_FINANCIALS;
SELECT COUNT(*), MAX(period_end) FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS;
SELECT * FROM PIPELINE_DB.MARTS.DIM_COMPANY;
SELECT COUNT(*), MAX(detected_at), MAX(mlflow_run_id)
FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES;
```

**Pass:**
- `RAW.COMPANY_FINANCIALS` row count > 0
- `MARTS.FCT_COMPANY_FINANCIALS` row count > 0, `MAX(period_end)` is a recent date
- `DIM_COMPANY` shows 3 rows: AAPL, MSFT, GOOGL
- `FCT_ANOMALIES` row count > 0, `MAX(detected_at)` = today

> **Where to find the mlflow_run_id:** Airflow UI → `stock_consumer_pipeline` → most recent run → `detect_anomalies` task → Logs → last line of stdout is a JSON dict with the `mlflow_run_id`.

> **Note on row count:** `write_results()` does DELETE + INSERT on every run, so the anomaly row count stays ~16. The `detected_at` timestamp is the proof of a fresh write.

---

## Step 10 — End-to-End: Weather Pipeline

Reset Kafka offset and date gate before triggering:
```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group weather-consumer-group \
    --reset-offsets --to-latest \
    --topic weather-hourly-raw --execute

kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set SF_WEATHER_LAST_WRITE_DATE ""
```

Trigger:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger API_Weather-Pull_Data
```

Poll (~2–3 min):
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs API_Weather-Pull_Data
```

Verify in Snowflake:
```sql
SELECT COUNT(*), MAX(imported_at) FROM PIPELINE_DB.RAW.WEATHER_HOURLY;
SELECT COUNT(*), MAX(time) FROM PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY;
```

**Pass:** Both have rows, timestamps are recent.

---

## Step 11 — MLflow Pod and UI

```bash
kubectl get pods -n airflow-my-namespace | grep mlflow
```

**Pass:** MLflow pod is `Running` with `1/1` READY.

Open SSH tunnel and check UI:
```bash
ssh -L 5500:localhost:5500 ec2-stock
```

Navigate to `http://localhost:5500/#/experiments/1` — you should see completed runs from Step 8.

### Step 11a — Verify MLflow Run Details

Navigate directly to a specific run using the `mlflow_run_id` from Step 9:
```
http://localhost:5500/#/experiments/1/runs/<mlflow_run_id>
```

Check these three things:

1. **Metrics visible** — The run page has a Metrics section with three rows: `n_anomalies`, `n_total`, `contamination_rate`.

2. **Artifact present** — Click the Artifacts tab. The left panel should show an `isolation_forest` folder.

3. **Model signature** — Expand the `isolation_forest` folder, click `MLmodel`. The YAML should show both input columns:
   ```
   inputs: '[{"type": "double", "name": "revenue_yoy_pct", "required": true},
             {"type": "double", "name": "net_income_yoy_pct", "required": true}]'
   ```

---

## Step 12 — OpenLineage Emitting Events

After Step 8, check the `dbt_run` task logs:
1. Airflow UI → `stock_consumer_pipeline` → most recent run → `dbt_run` task → **Logs**
2. Search for `"eventType"` in the log output

**Pass:** JSON blocks appear with `eventType` of `START` and `COMPLETE` — one pair per dbt model.

Or verify the package is installed:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/dbt-venv/bin/pip show openlineage-dbt
```

**Pass:** Prints `Name: openlineage-dbt` with a version number.

Check for errors:
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace | grep -i openlineage | tail -20
```

**Pass:** No `ERROR` lines mentioning openlineage.

---

## Step 13 — Dashboard Loads and Queries Data

Health check:
```bash
curl http://<EC2_PUBLIC_IP>:32147/health
```

**Pass:** Returns `{"status": "ok"}` with HTTP 200.

Open the dashboard: `http://<EC2_PUBLIC_IP>:32147/dashboard/`

**Pass:**
- Dropdown shows AAPL, MSFT, GOOGL
- Selecting a ticker renders the candlestick chart and stats table
- **Data Quality** tab shows the anomaly scatter plot (rows from FCT_ANOMALIES)
- Weather tab at `/weather/` renders the hourly forecast charts

Validation endpoint:
```bash
curl http://<EC2_PUBLIC_IP>:32147/validation
```

**Pass:** Returns JSON with `"status": "ok"`, row counts > 0, and recent timestamps.

---

## Step 14 — Staleness Monitor (Optional)

The staleness monitor is paused by default to save Snowflake costs. Trigger it directly without unpausing:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger Data_Staleness_Monitor
```

**Pass:** DAG completes with `success`.

> **Expected alert behavior:**
> - **Stocks** — `FCT_COMPANY_FINANCIALS` uses `filed_date` from SEC filings (often months old), so the staleness alert is expected and does not indicate a pipeline problem.
> - **Weather** — Should be fresh if Step 10 was just run. If an alert fires here, check that the weather write to Snowflake succeeded.

---

## Known Issues

**Anomaly detection SQL syntax error** — If the anomaly detection task fails with `unexpected '_MARTS_FCT_FIN'`, the query in `anomaly_detector.py` line 46 is missing the f-string prefix. Change `query = """` to `query = f"""`. Redeploy with `./scripts/deploy.sh --dags-only`.

---

## Quick Reference

| Step | Component | What it proves |
|------|-----------|---------------|
| 1 | Kubernetes | All pods are healthy |
| 2 | Kubernetes | Storage volumes attached |
| 3 | Snowflake | Connection works, schemas exist |
| 4 | Kafka | Topics exist, broker reachable |
| 5 | Airflow | Variables and secrets injected correctly |
| 6 | Airflow + ML | All DAGs parse, ml-venv installed, all tasks registered |
| 7 | dbt + ML | Models compile, anomaly detector runs standalone |
| 8 | Stocks pipeline | Full end-to-end: extract → Kafka → Snowflake → dbt → anomaly detection |
| 9 | Snowflake | Data populated in RAW, MARTS, ANALYTICS |
| 10 | Weather pipeline | Full end-to-end: extract → Kafka → Snowflake → dbt |
| 11 | MLflow | Experiment tracking pod up, run logged, metrics and artifacts present |
| 12 | OpenLineage | Lineage events emitting from dbt runs |
| 13 | Dashboard | UI loads, charts render, data is fresh |
| 14 | Alerting | Staleness monitor fires correctly |
