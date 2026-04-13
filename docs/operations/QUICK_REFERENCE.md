# Quick Reference

Common tasks with ready-to-copy commands.

---

### Deploy code changes
```bash
./scripts/deploy.sh
```
See [DEPLOY.md](../DEPLOY.md) for full details and options (e.g., `--dags-only` for fast DAG-only deploys).

### Check if DAGs are running
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list
```

### Access the UIs
```bash
# Open SSH tunnel (keep this terminal open):
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 -L 5500:localhost:5500 ec2-stock

# Then in your browser:
# Airflow UI:   http://localhost:30080
# Dashboard:    http://localhost:32147/dashboard/
# MLflow:       http://localhost:5500
```

### Check if pods are healthy
```bash
ssh ec2-stock kubectl get pods --all-namespaces
```
Every pod should show `Running` and `1/1` READY. If any show `CrashLoopBackOff`, `Error`, or `ImagePullBackOff`, something is wrong — see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Manually trigger the Stock pipeline
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
```

### Check data in Snowflake
Run in a Snowflake worksheet:
```sql
SELECT COUNT(*) FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS;
SELECT COUNT(*) FROM PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY;
```

### SSH won't connect from a new location
Your EC2 only allows SSH from one IP address (for security). When you're at a new location (different Wi-Fi), your IP changes. Go to AWS Console → EC2 → Security Groups → update the SSH rule with your new IP.

Or use Terraform to update it automatically:
```bash
./scripts/deploy.sh --provision
```
