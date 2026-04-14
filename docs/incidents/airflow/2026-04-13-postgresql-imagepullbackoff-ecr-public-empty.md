# PostgreSQL ImagePullBackOff — ECR Public Repository Has No Images

**Date:** 2026-04-13
**Severity:** High (deploy failed at Step 2f, all subsequent steps blocked)
**Affected component:** `airflow/helm/values.yaml` — PostgreSQL image configuration

---

## What was the problem

After switching to the new ARM-based spot instance (t4g.large), running `./scripts/deploy.sh --provision --snowflake-setup` failed at Step 2f with:

```
ERROR: PostgreSQL pod did not become Ready within 300s.
Back-off pulling image "public.ecr.aws/bitnami/postgresql:16"
Error: ImagePullBackOff
```

The PostgreSQL pod had been trying to pull its image for over 5 hours (1,339 failed attempts) and never succeeded.

Here is what happened, step by step:

1. The Helm chart for Airflow includes a built-in PostgreSQL database. It needs a specific type of image made by a company called Bitnami (a packager of open-source software). The image contains PostgreSQL plus Bitnami's own startup scripts.

2. An earlier fix (Bug 7 in the early bugs log) had pointed the image source at Amazon ECR Public (`public.ecr.aws/bitnami/postgresql:16`). The idea was that ECR Public would be faster and avoid Docker Hub's download limits.

3. It turns out that repository on ECR Public is completely empty — it has zero images. The image was never actually available from that address. On the old Intel server (t3.large), this never caused a problem because the image was already saved on the server's disk from an earlier download from Docker Hub. Kubernetes found the cached copy and used it without trying to download anything.

4. The new ARM spot instance starts with a completely blank disk every time it launches. There is nothing cached. Kubernetes tried to download the image from ECR Public, got back "not found," and kept retrying with increasing wait times between attempts (this is called "backoff"). After 5+ hours and over a thousand attempts, it was still failing.

5. Investigation found a second problem: Bitnami also removed all version-specific tags (like `16`, `16.8.0`, etc.) from Docker Hub. They migrated to a new distribution system and only left behind a single floating tag called `latest`. This means pinning to a specific PostgreSQL version is no longer possible with Bitnami images on Docker Hub.

6. The `latest` tag currently points to PostgreSQL 18 (not 16). This is safe for this project because the spot instance creates the database from scratch every time it launches — there is no existing data that needs to be upgraded from one PostgreSQL version to another.

---

## What was changed

**`airflow/helm/values.yaml`**

Switched the PostgreSQL image source from the empty ECR Public repository back to Docker Hub, using the only available tag (`latest`).

```yaml
# Before: pointed at a repository that has zero images
postgresql:
  image:
    registry: public.ecr.aws
    repository: bitnami/postgresql
    tag: "16"

# After: Docker Hub with the only surviving Bitnami tag
postgresql:
  image:
    registry: docker.io
    repository: bitnami/postgresql
    tag: "latest"
```

Why `latest` and not a specific version:
- Bitnami removed all versioned tags from Docker Hub (there is no `16`, `17`, or `18` tag)
- The `latest` tag is the only one that remains and it supports both Intel (amd64) and ARM (arm64)
- Using the official PostgreSQL image (`postgres:16`) is not an option because the Airflow Helm chart's built-in database setup expects Bitnami's custom startup scripts and configuration format

Why PostgreSQL 18 (the current `latest`) is safe:
- On a spot instance, the database is always created fresh — there is never existing data to migrate between versions
- Airflow 3.x supports modern PostgreSQL versions
- The schema is created by the migration job, which builds the correct tables regardless of the PostgreSQL version

---

## Why this didn't happen before

On the long-running Intel server (t3.large) that predated the spot instance setup, the PostgreSQL image had been downloaded from Docker Hub months ago when the versioned tags still existed. It was stored in the server's local image cache. Kubernetes found the cached copy every time and never needed to contact ECR Public.

The spot instance Auto Scaling Group (introduced on 2026-04-13) launches a fresh ARM server with an empty disk. With nothing cached, Kubernetes had to download the image for real — and discovered that the source it was pointed at had nothing to offer.

---

## Files changed

| File | Change |
|------|--------|
| `airflow/helm/values.yaml` | PostgreSQL image: `public.ecr.aws/bitnami/postgresql:16` → `docker.io/bitnami/postgresql:latest` |
| `docs/incidents/airflow/early-bugs-config-and-infra.md` | Bug 7: added note that the ECR Public fix was superseded by this fix |
