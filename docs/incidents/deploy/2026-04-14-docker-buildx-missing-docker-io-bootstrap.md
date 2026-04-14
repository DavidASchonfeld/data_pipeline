# Docker Build Frozen 8+ Minutes — BuildKit Plugin Missing After Auto-Bootstrap

**Date:** 2026-04-14
**Severity:** High (Step 2b2 failed; Airflow image never built; all downstream steps terminated)
**Affected component:** `scripts/deploy/bootstrap.sh` — Docker installation on fresh spot instances

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy appeared to freeze for over 8 minutes at the Docker build step, then eventually failed with:

```
ERROR: BuildKit is enabled but the buildx component is missing or broken.
✗ Airflow Docker build + K3S import (Step 2b2) FAILED
```

Here is what happened, step by step:

1. The deploy ran the new auto-bootstrap (added earlier the same day) on a fresh spot instance. The bootstrap installed Docker using Ubuntu's built-in package called `docker.io`.

2. The Kafka, MLflow, and Flask steps were kicked off in parallel while the Airflow image build ran separately.

3. The image build step ran `DOCKER_BUILDKIT=1 docker build ...` on the EC2 instance to build the custom Airflow container image.

4. `DOCKER_BUILDKIT=1` tells Docker to use its modern "BuildKit" build system instead of the older legacy one. BuildKit requires a separate component called `docker-buildx-plugin` to be installed alongside Docker.

5. Ubuntu's `docker.io` package — the version you get when you just run `apt install docker.io` — does not include `docker-buildx-plugin`. This plugin is only available through Docker's own official installation channel.

6. When Docker saw `DOCKER_BUILDKIT=1` but could not find the buildx plugin, it did not fail immediately. Instead it appeared to hang — waiting for a component that was never going to respond — for over 8 minutes before finally printing the error and giving up.

7. When the main deploy process detected the failure, it sent a termination signal to the parallel Kafka, MLflow, and Flask steps, which is why those showed "Terminated: 15" in the output. Those steps had actually completed successfully by that point — the termination was just the cleanup signal from the failed main step.

---

## What was changed

**`scripts/deploy/bootstrap.sh`**

Changed the Docker installation from `docker.io` (Ubuntu's built-in package) to Docker CE from Docker's official apt repository. The official repository includes all required components: the Docker engine, the CLI, the containerd runtime, and the buildx plugin.

```bash
# Before: used docker.io from Ubuntu's default apt repos — missing buildx
sudo apt-get install -y mariadb-server docker.io unzip curl
sudo systemctl enable --now mariadb docker

# After: installs Docker CE from Docker's official apt repo — includes buildx
sudo apt-get install -y mariadb-server unzip curl ca-certificates gnupg
# Add Docker's official GPG key and apt repository
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
# Add the repo, auto-detecting Ubuntu version (jammy, noble, etc.)
. /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=...] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo systemctl enable --now docker
```

The auto-detection of Ubuntu version (`$VERSION_CODENAME`) means this works correctly whether the spot instance is running Ubuntu 22.04, 24.04, or a future release.

---

## Why this didn't happen before

On the old long-running t3.large server, Docker was installed manually during the original setup, and the buildx plugin was present. The auto-bootstrap introduced on 2026-04-14 was the first time Docker was installed programmatically as part of the deploy, and it used the simpler `docker.io` package that most guides mention — without knowing it was missing a critical component.

The failure only surfaced on fresh spot instances (where the auto-bootstrap runs) because existing instances already had a working Docker installation from before.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/bootstrap.sh` | Replaced `docker.io` install with Docker CE from official apt repo; now includes `docker-buildx-plugin` |
