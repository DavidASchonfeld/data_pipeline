# Incident: Airflow Down After a Spot Replacement, and the Redeploy Failed Because the Helm Chart Link Went 404

**Date:** 2026-11-21
**Severity:** Medium (Airflow scheduler, API server, Postgres and Kafka down for ~6 hours; the dashboard, chat service and pgvector stayed up)
**Status:** Resolved in source: the deploy now fetches the pinned chart file itself, with a mirror fallback and a checksum check

---

## Summary

AWS replaced my spot instance at 20:24 UTC. The new machine has a new hostname, so K3s registered a new node name (`ip-172-31-84-54`). The disk volumes for Airflow's Postgres and for Kafka were still pinned to the old node (`ip-172-31-64-52`), so both pods sat in `Pending`. Everything that waits on Postgres (scheduler, triggerer, dag-processor, API server) crash-looped in its init containers.

The deploy scripts already know how to fix this (`_cleanup_stale_pg_pvc`, `_cleanup_stale_kafka_pvc`), so a plain full deploy should have been enough. That deploy failed 22 minutes in with:

```
Error: failed to fetch https://dist.apache.org/repos/dist/release/airflow/helm-chart/1.20.0/airflow-1.20.0.tgz : 404 Not Found
```

This second problem had nothing to do with the spot replacement. Every deploy would have hit it.

---

## Root cause

**1. Pinned volumes (expected, already handled).** K3s's built-in `local-path` storage ties each volume to the node that first created it. A new node name means the old volume can't be used. The deploy cleans this up, so this part only needed a deploy.

**2. The chart link moved (new).** I pin the Airflow Helm chart at version `1.20.0` and installed it as `apache-airflow/airflow --version 1.20.0`. Helm looks that version up in the repo index at `airflow.apache.org`, which still lists the download as `dist.apache.org/.../1.20.0/airflow-1.20.0.tgz`. Apache only keeps current releases on `dist.apache.org`. Once newer charts shipped (1.21.0, 1.22.0), 1.20.0 moved to `archive.apache.org`, but the index entry wasn't updated. The pin I relied on for stability became a link to nothing.

---

## Fix

`scripts/deploy/airflow_pods.sh` has a new helper, `_ensure_airflow_chart`, called at the start of the Helm step:

- It downloads `airflow-1.20.0.tgz` onto the EC2 host once (`~/airflow/helm/charts/`), trying `dist.apache.org` first and then `archive.apache.org`.
- It checks the file against Apache's published `.sha512` checksum and deletes any download that doesn't match, so a bad or partial file is never installed.
- Later deploys reuse the cached file (after confirming `helm show chart` can read it), so the deploy no longer depends on the chart index at all.
- `helm upgrade` and the `helm template` used for migration-job recovery both point at that local file instead of `apache-airflow/airflow --version 1.20.0`.
- If neither mirror has the chart, the deploy stops before `helm upgrade` with a clear error instead of failing partway through.

I kept the chart at 1.20.0 on purpose. Moving to a newer chart is a separate change, with its own testing.

---

## Side effect worth knowing

The volume cleanup recreates Postgres and Kafka storage empty. After a spot replacement, Airflow's run history and any unconsumed Kafka messages are gone. The real data (Snowflake, pgvector, MLflow) isn't affected. A lasting fix would give K3s a fixed node name, so volumes survive a new hostname. That's a Terraform user-data change, which I've logged for later.

---

## Lessons

- **A version pin only protects you if the artifact behind it stays reachable.** Pinning a version in a repo index still depends on someone else keeping that index accurate. Downloading the exact file with a checksum check removes that dependency.
- **Check older mirrors first when a pinned release 404s.** Apache, PyPI mirrors and distro repos often move old releases to an archive rather than deleting them.
