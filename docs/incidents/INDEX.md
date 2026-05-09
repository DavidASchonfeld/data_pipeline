# Incident Log

A record of past issues, what caused them, and how they were fixed. Use this when debugging — if you've seen the error before, the fix is documented here.

Incidents are organized by component. Each file contains the symptom, root cause, and fix.

For the failure mode catalog (what *can* go wrong, not what *did* go wrong), see [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md).

---

## By Component

### Airflow

| Date | Issue | File |
|------|-------|------|
| 2026-03-30 | PostgreSQL image, PV path, DB credential fixes | [2026-03-30/](airflow/2026-03-30/) |
| 2026-03-31 | Stock DAG disappearing: config drift, processor cache | [2026-03-31/](airflow/2026-03-31/) |
| 2026-04-06 | Airflow 3.x upgrade: 7 cascading root causes | [airflow-3x-upgrade-learnings](airflow/2026-04-06-airflow-3x-upgrade-learnings.md) |
| 2026-04-07 | ImagePullBackOff: obsolete images, invalid YAML | [imagepullbackoff-incident](airflow/2026-04-07-airflow-imagepullbackoff-incident.md) |
| 2026-04-08 | DAGs stuck in "Up for Retry": missing alerting module | [incident_dag_up_for_retry](airflow/incident_dag_up_for_retry_Apr8_2026.md) |
| 2026-04-10 | ErrImageNeverPull: K3S garbage-collected custom image | [errimagenevrpull](airflow/2026-04-10-airflow-errimagenevrpull-image-gc.md) |
| 2026-04-10 | Helm post-upgrade hook timeout | [helm-hook-timeout](airflow/2026-04-10-helm-post-upgrade-hook-timeout.md) |
| 2026-04-10 | Helm migration job ownership metadata | [helm-migration-job](airflow/2026-04-10-helm-migration-job-ownership-metadata.md) |
| 2026-04-10 | Anomaly detector: revenue column missing | [revenue-column](airflow/2026-04-10-anomaly-detector-revenue-column-missing.md) |
| 2026-04-10 | Anomaly detection: three errors (warnings, pct_change, permissions) | [three-errors](airflow/2026-04-10-anomaly-detection-three-errors.md) |
| 2026-04-10 | Dockerfile pip warnings (cache, backtracking) | [dockerfile-pip](airflow/2026-04-10-dockerfile-pip-warnings.md) |
| 2026-04-10 | Docker build: pkg_resources + pip 26 interaction | [docker-pkg-resources](airflow/2026-04-10-docker-build-pkg-resources-pip26.md) |
| 2026-04-10 | ml-venv: setuptools 82 removed pkg_resources | [setuptools-82](airflow/2026-04-10-ml-venv-setuptools-82-pkg-resources-removed.md) |
| 2026-04-10 | ml-venv: pkg_resources install order bug | [install-order](airflow/2026-04-10-ml-venv-pkg-resources-install-order.md) |
| 2026-04-10 | pip cache permission warning | [pip-cache](airflow/2026-04-10-pip-cache-permission-warning.md) |
| 2026-04-10 | pkg_resources deprecation warning | [pkg-resources-deprecation](airflow/2026-04-10-pkg-resources-deprecation-warning.md) |
| 2026-04-10 | RequestsDependencyWarning (urllib3/chardet) | [requests-dependency](airflow/2026-04-10-requests-dependency-warning.md) |
| 2026-04-11 | sklearn feature name warnings in anomaly detector | [sklearn-warnings](airflow/2026-04-11-anomaly-detector-sklearn-feature-name-warnings.md) |
| 2026-04-11 | Verification step fixes (Steps 3, 5, 7, 8, 9, 10) | [verification-fixes](airflow/2026-04-11-verification-step-fixes.md) |
| 2026-04-13 | ErrImageNeverPull: multiple old ReplicaSets after spot redeploy | [errimagenevrpull-multi-rs](airflow/2026-04-13-errimagenevrpull-multiple-old-replicasets.md) |
| 2026-04-13 | Pods stuck in Init:0/1: wait-for-airflow-migrations timed out on cold Postgres | [wait-for-migrations](airflow/2026-04-13-wait-for-migrations-timeout.md) |
| 2026-04-13 | Migration job timeout: kubectl wait ignores Failed jobs | [kubectl-wait-blind-spot](airflow/2026-04-13-migration-job-timeout-kubectl-wait-blind-spot.md) |
| 2026-04-13 | PostgreSQL ImagePullBackOff: ECR Public repository empty | [postgresql-imagepullbackoff](airflow/2026-04-13-postgresql-imagepullbackoff-ecr-public-empty.md) |
| — | Early bugs: config drift, PV path, API limits, probes (Bugs 1–8) | [early-bugs-config](airflow/early-bugs-config-and-infra.md) |
| — | Early bugs: OOMKill, upgrade, missing secret, probes (Bugs 9–16) | [early-bugs-upgrade](airflow/early-bugs-upgrade-and-migration.md) |
| — | OpenLineage dbt fixes | [openlineage-fixes](airflow/OPENLINEAGE_DBT_FIXES.md) |
| — | dbt deprecation fixes (Airflow 3.x) | [dbt-deprecation](airflow/T3_DBT_DEPRECATION_FIXES.md) |

### MLflow

| Date | Issue | File |
|------|-------|------|
| 2026-04-10 | pkg_resources missing after setuptools update | [pkg-resources](mlflow/2026-04-10-mlflow-pkg-resources-missing.md) |
| 2026-04-10 | Fix not propagating (image caching) | [fix-not-propagating](mlflow/2026-04-10-mlflow-fix-not-propagating.md) |
| 2026-04-10 | Artifact permission error + chardet warning | [artifact-permission](mlflow/2026-04-10-mlflow-artifact-permission-and-chardet-warning.md) |
| 2026-04-10 | Deleted experiment cannot be set | [deleted-experiment](mlflow/2026-04-10-mlflow-deleted-experiment-cannot-set.md) |
| 2026-04-10 | Restore logic undoes artifact root fix | [restore-logic](mlflow/2026-04-10-mlflow-restore-logic-undoes-artifact-root-fix.md) |
| 2026-04-10 | Ephemeral storage eviction | [storage-eviction](mlflow/2026-04-10-mlflow-ephemeral-storage-eviction.md) |
| 2026-04-10 | Node taint: unschedulable (disk pressure) | [node-taint](mlflow/2026-04-10-mlflow-node-taint-unschedulable.md) |
| 2026-04-10 | SSH tunnel to ClusterIP | [ssh-tunnel](mlflow/2026-04-10-mlflow-ui-ssh-tunnel-clusterip.md) |
| 2026-04-10 | Port-forward SSH exit 255 (three calls) | [portforward-exit255](mlflow/2026-04-10-mlflow-portforward-ssh-exit255-three-calls.md) |
| 2026-04-10 | Port-forward pgrep warning | [pgrep-warning](mlflow/2026-04-10-mlflow-portforward-pgrep-warning.md) |
| 2026-04-10 | Port-forward fuser stdout + pgrep verification | [fuser-pgrep](mlflow/2026-04-10-mlflow-portforward-fuser-stdout-pgrep-verification.md) |
| 2026-04-10 | Step 7c connection refused (pod not ready) | [connection-refused](mlflow/2026-04-10-step7c-mlflow-connection-refused-not-ready.md) |
| 2026-04-11 | Input example signature warning | [input-example](mlflow/2026-04-11-mlflow-input-example-signature-warning.md) |
| — | MLflow server live error log | [error-log](mlflow/mlflow-server-live-error-log.md) |

### Deploy Script

| Date | Issue | File |
|------|-------|------|
| 2026-04-10 | Unbound variable (awk pattern) | [unbound-variable](deploy/2026-04-10-deploy-sh-unbound-variable-awk.md) |
| 2026-04-10 | Two bugs: shell quoting + MLflow SQLite constraint | [two-bugs](deploy/2026-04-10-deploy-sh-two-bugs-ml-venv-and-mlflow-sqlite.md) |
| 2026-04-10 | Warning and error summary capture | [warning-summary](deploy/2026-04-10-deploy-sh-warning-summary.md) |
| 2026-04-10 | No elapsed time display | [elapsed-time](deploy/2026-04-10-deploy-sh-no-elapsed-time.md) |
| 2026-04-10 | Step ordering: 2c must run before 2d | [step-ordering](deploy/2026-04-10-deploy-sh-step-ordering-2c-after-2d.md) |
| 2026-04-10 | Port-forward exit 255 + silent failure | [portforward-failure](deploy/2026-04-10-deploy-sh-portforward-exit255-silent-failure.md) |
| 2026-04-10 | Parallelization speedup (22 min → 7-10 min) | [parallelization](deploy/2026-04-10-deploy-sh-parallelization-speedup.md) |
| 2026-04-14 | chmod K3s config fails on fresh spot instance | [chmod-k3s-not-found](deploy/2026-04-14-chmod-k3s-yaml-not-found-fresh-spot.md) |
| 2026-04-14 | --provision does not auto-bootstrap fresh spot instances | [provision-no-bootstrap](deploy/2026-04-14-provision-no-auto-bootstrap-fresh-spot.md) |
| 2026-04-14 | Docker build frozen 8+ min: buildx plugin missing after auto-bootstrap | [docker-buildx-missing](deploy/2026-04-14-docker-buildx-missing-docker-io-bootstrap.md) |
| 2026-04-14 | Buildx still missing: old bootstrap left docker.io, no pre-flight check | [buildx-no-precheck](deploy/2026-04-14-buildx-missing-no-precheck-old-bootstrap.md) |
| 2026-04-14 | Docker daemon not running: pre-flight only checked plugin, not service | [docker-daemon-not-running](deploy/2026-04-14-docker-daemon-not-running-no-precheck.md) |
| 2026-04-14 | Docker daemon down between pre-flight check and build + terminal freeze | [docker-daemon-down-between](deploy/2026-04-14-docker-daemon-down-between-precheck-and-build.md) |
| 2026-04-14 | Kafka offset reset Java TimeoutException for non-existent consumer groups | [kafka-offset-nonexistent-group](deploy/2026-04-14-kafka-offset-reset-timeout-nonexistent-group.md) |
| 2026-04-17 | Deploy appears frozen after "Migration job complete." (_wait_bg silent wait) | [frozen-after-migration](deploy/2026-04-17-deploy-frozen-after-migration-complete.md) |
| 2026-04-17 | Docker build failed: server overloaded by 3 parallel jobs (BuildKit EOF + containerd socket reset) | [docker-build-containerd-overload](deploy/2026-04-17-docker-build-failed-containerd-overload-parallel-jobs.md) |
| 2026-04-17 | MLflow deploy failed: Docker layer extraction corruption + kubectl permission denied (parallel job overload) | [mlflow-deploy-failed-parallel-overload](deploy/2026-04-17-mlflow-kubectl-permission-denied-k3s-restart.md) |
| 2026-04-17 | Deploy warnings: stale containerd lease error + port 5500 already in use on port-forward restart | [containerd-lease-port5500](deploy/2026-04-17-containerd-lease-error-and-port5500-in-use.md) |

### Snowflake

| Date | Issue | File |
|------|-------|------|
| 2026-04-08 | RBAC schema grants (role vs user ownership) | [rbac-grants](snowflake/2026-04-08-snowflake-rbac-schema-grants.md) |
| 2026-04-08 | Non-deterministic dedup in fct_company_financials | [dedup](snowflake/2026-04-08-fct-financials-nondeterministic-dedup.md) |
| 2026-04-07 | Weather Snowflake write failure (timestamp mismatch) | [weather-write](snowflake/2026-04-07-weather-snowflake-write-failure.md) |
| 2026-04-10 | FCT_ANOMALIES insufficient privileges | [privileges](snowflake/2026-04-10-snowflake-fct-anomalies-insufficient-privileges.md) |

### Kafka

| Date | Issue | File |
|------|-------|------|
| 2026-04-10 | Image not pre-pulled, rollout timeout | [image-prepull](kafka/2026-04-10-kafka-image-not-prepulled-rollout-timeout.md) |
| — | CPU starvation after Kafka deploy | [cpu-starvation](../../kafka/INCIDENT_CPU_STARVATION_APR2026.md) |
| — | Helm + Kafka conflict | [helm-conflict](../../kafka/INCIDENT_HELM_KAFKA_CONFLICT_APR2026.md) |

### Kubernetes

| Date | Issue | File |
|------|-------|------|
| 2026-04-10 | kubectl exec: blank line after backslash | [blank-line](kubernetes/2026-04-10-kubectl-exec-blank-line-continuation.md) |
| 2026-04-10 | kubectl exec: container not found (pod initializing) | [container-not-found](kubernetes/2026-04-10-kubectl-exec-container-not-found-scheduler.md) |
| 2026-04-10 | kubectl exec: scheduler not ready for Kafka variable | [not-ready](kubernetes/2026-04-10-kubectl-exec-scheduler-not-ready-kafka-variable.md) |
| 2026-04-10 | Missing SSH wrapper for Kafka variable | [missing-ssh-kafka](kubernetes/2026-04-10-kubectl-missing-ssh-kafka-variable.md) |
| 2026-04-10 | Missing SSH wrapper for MLflow URI | [missing-ssh-mlflow](kubernetes/2026-04-10-kubectl-missing-ssh-mlflow-tracking-uri.md) |
| 2026-04-10 | Port-forward SSH exit 255 | [portforward-255](kubernetes/2026-04-10-kubectl-port-forward-ssh-exit-255.md) |
| 2026-04-10 | No space left on device (K3S containerd import) | [no-space](kubernetes/2026-04-10-no-space-left-on-device-k3s-containerd-import.md) |

### Dashboard

| Date | Issue | File |
|------|-------|------|
| 2026-04-11 | Anomaly detection section added | [anomaly-section](dashboard/2026-04-11-dashboard-anomaly-detection-section.md) |
| 2026-04-11 | Financials Snowflake role issue | [snowflake-role](dashboard/2026-04-11-dashboard-financials-snowflake-role.md) |
| 2026-04-11 | Loading spinner | [loading-spinner](dashboard/2026-04-11-dashboard-loading-spinner.md) |
| 2026-04-11 | Snowflake cache strategy | [cache-strategy](dashboard/2026-04-11-dashboard-snowflake-cache-strategy.md) |
| 2026-04-15 | Dashboard goes blank during spot replacement (recovery.js added) | [blank-page-spot](dashboard/2026-04-15-dashboard-blank-page-spot-replacement.md) |
| 2026-04-15 | recovery.js syntax error preventing overlay | [recovery-js-syntax](dashboard/2026-04-15-recovery-js-syntax-error.md) |
| 2026-04-15 | Offline banner showing incorrectly on startup | [offline-banner-startup](dashboard/2026-04-15-offline-banner-on-startup.md) |
| 2026-04-15 | "Reconnecting" overlay not appearing during spot replacement | [overlay-not-showing](dashboard/2026-04-15-recovery-js-overlay-not-showing.md) |
| 2026-04-15 | Slow startup + "Callback failed" on cold worker cache | [slow-startup-callbacks](dashboard/2026-04-15-slow-startup-callback-failures.md) |
| 2026-04-15 | "Callback failed" on weather nav: console.error not caught by window listener | [weather-nav-callback](dashboard/2026-04-15-weather-nav-callback-failed.md) |
| 2026-04-15 | recovery.js reload loop on callback fail during navigation | [reload-loop](dashboard/2026-04-15-weather-nav-callback-reload-loop.md) |
| 2026-04-16 | "encountered bad format: +.1f" in weather chart hover tooltip | [weather-chart-bad-format](dashboard/2026-04-16-weather-chart-bad-format-deviation.md) |

### Features

| Date | Feature | File |
|------|---------|------|
| 2026-04-15 | Interactive anomaly table — legend sync & column sorting | [anomaly-table-sorting](features/2026-04-15-anomaly-table-interactive-sorting-and-graph-sync.md) |
| 2026-04-15 | Weather dropdown investigation + server-offline banner | [offline-banner](features/2026-04-15-weather-dropdown-and-offline-banner.md) |

### Infrastructure

| Date | Issue | File |
|------|-------|------|
| 2026-04-08 | EBS disk full (K3S garbage-collected images) | [ebs-disk-full](infrastructure/2026-04-08-ebs-disk-full.md) |
| 2026-04-10 | Ubuntu system updates | [ubuntu-updates](infrastructure/2026-04-10-ubuntu-system-updates-ec2.md) |
| 2026-04-13 | Spot + ASG + ARM cost optimization (73% reduction) | [spot-asg-arm](infrastructure/2026-04-13-spot-asg-arm-cost-optimization.md) |
| 2026-04-15 | Dashboard not publicly reachable after deploy | [not-publicly-reachable](infrastructure/2026-04-15-dashboard-not-publicly-reachable.md) |
| 2026-04-15 | CloudFront showing empty graphs + SyntaxError (4 fixes) | [cloudfront-empty-graphs](infrastructure/2026-04-15-cloudfront-empty-graphs-syntax-error.md) |
| 2026-04-16 | K3s API server "connection refused" on rapid redeploy | [k3s-api-connection-refused](infrastructure/2026-04-16-k3s-api-server-connection-refused-on-redeploy.md) |
| 2026-04-16 | Migration job timeout + image import lease error | [migration-timeout-lease-error](infrastructure/2026-04-16-migration-job-timeout-and-image-lease-error.md) |
| 2026-04-16 | dag-processor "still 2 pods after 5 min" warning on rapid redeploy | [dag-processor-pods-not-scaled-down](infrastructure/2026-04-16-dag-processor-pods-not-scaled-down.md) |
| 2026-04-17 | Deploy fails: SSH unreachable after 36 attempts (stale host key after spot replacement) | [ssh-host-key-mismatch](infrastructure/2026-04-17-ssh-host-key-mismatch-spot-replacement.md) |
| 2026-05-08 | Migration job 900s timeout: kubelet evicted pod, image GC'd from containerd, retry pod stuck | [migration-disk-pressure-evict](infrastructure/2026-05-08-migration-disk-pressure-evict-image-gc.md) |
| 2026-05-08 | Migration recovery `AlreadyExists` race: finalizing Job invisible to `kubectl get` but still in etcd; fix: `_wait_mig_gone` helper with finalizer-stripping fallback | [migration-disk-pressure-evict#follow-up](infrastructure/2026-05-08-migration-disk-pressure-evict-image-gc.md#follow-up-alreadyexists-after-delete-may-8-redeploy) |
| 2026-05-08 | `AlreadyExists` again + MLflow 14-pod eviction storm: recovery deleted whole Job (fought Job controller); fix: restart pod only, server-side apply, MLflow rollout polling loop | [migration-disk-pressure-evict#follow-up-3](infrastructure/2026-05-08-migration-disk-pressure-evict-image-gc.md#follow-up-3-may-8-third-redeploy--same-day) |

---

## Archive

Older changelog entries: [_archive/CHANGELOG_ARCHIVE.md](_archive/CHANGELOG_ARCHIVE.md)
