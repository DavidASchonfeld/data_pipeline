# T3 OpenLineage — Verification Checklist

## 1. Deploy
```bash
./scripts/deploy.sh
```
Rebuilds `airflow-dbt:3.1.8-dbt` with `openlineage-dbt` baked in and restarts Airflow pods.

---

## 2. Confirm package installed
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  /opt/dbt-venv/bin/pip show openlineage-dbt
```
**Pass:** prints `Name: openlineage-dbt` with a version number.

---

## 3. Trigger DAG and check for OpenLineage events
The `stock_consumer_pipeline` DAG emits OpenLineage events when it runs. Trigger it manually:
1. Airflow UI → `stock_consumer_pipeline` → trigger manually (or use CLI: `airflow dags trigger stock_consumer_pipeline`)
2. Check scheduler logs for OpenLineage events:
   ```bash
   kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c scheduler --since=2m | grep openlineage
   ```
3. Look for OpenLineage JSON events with `eventType` of `START` and `COMPLETE`, e.g.:
   ```json
   {"eventType": "START", "job": {"namespace": "pipeline", ...}, "inputs": [...], "outputs": [...]}
   ```
   One START event at DAG start, one COMPLETE event at DAG completion.

**Pass:** OpenLineage events appear in logs with `eventType` fields. **Fail:** no events → verify openlineage-dbt package is installed (Step 2) and check for errors in scheduler logs.

---

## 4. No errors in scheduler logs
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace | grep -i openlineage | tail -20
```
**Pass:** no `ERROR` lines mentioning openlineage.

---

## 5. Dashboard still loads
Open `http://localhost:32147/dashboard/` — charts should render as before. T3 doesn't touch Snowflake or the dashboard; this is just a sanity check.
