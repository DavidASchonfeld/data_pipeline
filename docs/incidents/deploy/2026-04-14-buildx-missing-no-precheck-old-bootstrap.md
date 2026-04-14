# Docker Buildx Still Missing — Old Bootstrap Left docker.io in Place and Deploy Had No Safety Check

**Date:** 2026-04-14
**Severity:** High (Step 2b2 failed; Airflow image never built; all downstream steps terminated)
**Affected component:** `scripts/deploy/setup.sh`, `scripts/bootstrap_ec2.sh`

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy failed at the Docker build step with the same error as the earlier buildx incident:

```
ERROR: BuildKit is enabled but the buildx component is missing or broken.
✗ Airflow Docker build + K3S import (Step 2b2) FAILED
```

Here is what happened, step by step:

1. An earlier fix (the "Docker Build Frozen 8+ Minutes" incident from the same day) had already updated the auto-bootstrap module (`scripts/deploy/bootstrap.sh`) to install Docker CE from Docker's official repository instead of Ubuntu's `docker.io` package. Docker CE includes the `docker-buildx-plugin` that the build needs.

2. However, the auto-bootstrap only runs when K3s is not installed on the instance. It checks for the file `/etc/rancher/k3s/k3s.yaml` — if that file exists, the auto-bootstrap is skipped entirely.

3. In this case, the instance had already been set up before the fix was made — either by a previous deploy run or by running the standalone `bootstrap_ec2.sh` script. Both of those earlier paths installed Docker using Ubuntu's `docker.io` package (without the buildx plugin). K3s was installed correctly, so everything else worked.

4. When the new deploy ran with `--provision`, the deploy saw that K3s was already present and skipped the auto-bootstrap. It moved straight to the Docker build step, which still had the old `docker.io` without buildx. The build froze for several minutes and then failed.

5. In short: the earlier fix prevented the problem on brand-new instances, but did nothing for instances that were already set up with the old Docker installation. There was no safety check anywhere in the deploy that would catch this gap.

6. There was also a second gap: the standalone `bootstrap_ec2.sh` script (used for manual bootstraps) was never updated — it still installed `docker.io`. Anyone using that script to set up a new instance would hit the same buildx problem on their next deploy.

---

## What was changed

**`scripts/deploy/setup.sh`** — Added a new pre-flight check (Step 1c2) that runs on every deploy, right after the K3s check. It asks Docker on the EC2 instance whether the buildx plugin is available by running `docker buildx version`. If the command fails, the deploy automatically upgrades Docker:

- Removes the old `docker.io` package
- Adds Docker's official apt repository (with GPG key, auto-detecting Ubuntu version)
- Installs Docker CE with the buildx plugin
- Verifies the upgrade worked before continuing

This check runs regardless of whether the auto-bootstrap ran. It catches all cases: instances bootstrapped before the fix, instances set up with the old `bootstrap_ec2.sh`, or any other situation where Docker is installed but buildx is missing.

If Docker already has buildx (which it will on instances set up after the fix), the check passes instantly and moves on — it adds no delay to a normal deploy.

**`scripts/bootstrap_ec2.sh`** — Replaced the `docker.io` installation with Docker CE from Docker's official apt repository, the same way the auto-bootstrap module already does it. This means anyone using the standalone script to set up a new instance will get Docker CE with buildx included from the start.

```bash
# Before: installed docker.io from Ubuntu's apt repos — missing buildx plugin
sudo apt-get install -y mariadb-server docker.io unzip curl

# After: installs Docker CE from Docker's official repo — includes buildx
sudo apt-get install -y mariadb-server unzip curl ca-certificates gnupg
# (Docker CE installed separately via Docker's official apt repo)
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
```

---

## Why this didn't happen before

The earlier buildx fix only addressed one path: the auto-bootstrap that runs on fresh spot instances during `--provision`. It did not add any check for instances that were already set up. Since K3s was present, the auto-bootstrap was skipped, and the old `docker.io` installation (without buildx) remained in place.

The standalone `bootstrap_ec2.sh` was also overlooked during the earlier fix because it is a separate script that runs independently of the deploy pipeline. It is typically used once during the initial manual setup of a new instance, so the gap was not immediately obvious.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/setup.sh` | Added Step 1c2: pre-flight check for Docker buildx; auto-upgrades docker.io to Docker CE if missing |
| `scripts/bootstrap_ec2.sh` | Replaced `docker.io` with Docker CE from official apt repo (same as auto-bootstrap module) |
