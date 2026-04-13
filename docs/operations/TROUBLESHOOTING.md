# Troubleshooting Guide

Start here when something breaks. This guide covers both the debugging approach (how to think about problems) and specific issue solutions.

**Related docs:**
- [FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md) — What *can* go wrong, per component
- [Incident Log](../incidents/INDEX.md) — What *did* go wrong, and how it was fixed
- [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md) — Checklists to prevent issues
- [COMMANDS.md](../reference/COMMANDS.md) — Shell command reference
- [GLOSSARY.md](../reference/GLOSSARY.md) — Term definitions

---

## Debugging Approach

For a systematic approach to debugging, including mental models and diagnostic sequences:

| Guide | What's inside |
|-------|---------------|
| [Approach & Mental Model](debugging/approach.md) | Three-layer traffic path, namespaces, common gotchas |
| [Diagnostic Sequences](debugging/diagnostic-sequences.md) | Step-by-step diagnostic commands, Airflow 3.x gotchas, log reading, health checks |
| [Common Issues A-I](debugging/common-issues-1.md) | PermissionError, endpoints `<none>`, ImagePullBackOff, Init:0/1, DAG paused, DB access denied, UI unreachable, empty dashboard, deprecation warnings, CrashLoopBackOff |
| [Common Issues J-N](debugging/common-issues-2.md) | rsync mkdir failure, weather DAG load errors, OOMKill static assets, 404 UI bug, pymysql missing |

---

## Specific Issue Solutions

| File | Covers |
|------|--------|
| [Airflow DAG Issues — Discovery](troubleshooting/airflow-dag-issues.md) | DagBag errors, parse failures, deprecation warnings, DAG not discoverable, Variable.get changes |
| [Airflow DAG Issues — Runtime](troubleshooting/airflow-dag-runtime-issues.md) | DAG disappearing after deploy, dynamic start_date, processor cache staleness, task failures, task state sync |
| [Kubernetes Pod Issues](troubleshooting/kubernetes-pod-issues.md) | Pod crashes, OOMKill, CrashLoopBackOff, CreateContainerConfigError, Helm upgrade stuck, service selector mismatch |
| [Deploy Issues](troubleshooting/deploy-issues.md) | deploy.sh warnings, DAG validation, DAG files not visible, changes not reflected in cluster |
| [Docker Build Issues](troubleshooting/docker-build-issues.md) | BuildKit/buildx missing, Docker build failures on EC2 |
| [System Issues](troubleshooting/system-issues.md) | apt freeze, SSH warnings, kubectl permissions, browser console errors, 404 bookmark URLs |

---

## Common Commands Reference

### Check Everything is Running

```bash
# Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# Scheduler pod logs
ssh ec2-stock kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50

# PersistentVolume status
ssh ec2-stock kubectl get pv,pvc -A | grep dag

# K3S cluster status
ssh ec2-stock kubectl cluster-info
ssh ec2-stock kubectl get nodes
```

### Manual DAG Trigger (if needed)

```bash
# Trigger specific DAG run from EC2
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' 'Stock_Market_Pipeline'"
```

### Check Snowflake Data

Run in a Snowflake worksheet:
```sql
SELECT COUNT(*) FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS;
SELECT COUNT(*) FROM PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY;
```

---

## Prevention Checklist

When making infrastructure changes:

- [ ] Update `deploy.sh` paths
- [ ] Update K8s manifests to match
- [ ] Test `deploy.sh` with dry-run or test branch first
- [ ] Verify files on EC2 after deploy
- [ ] Verify files in pod after pod restart
- [ ] Check Airflow logs for DAG parsing errors
- [ ] Monitor first DAG run for execution errors
