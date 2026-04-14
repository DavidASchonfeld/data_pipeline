# Deploy Failed at Step 1c — K3s Config File Not Found on Fresh Spot Instance

**Date:** 2026-04-14
**Severity:** High (deploy failed at Step 1c, all subsequent steps blocked)
**Affected component:** `scripts/deploy/setup.sh` — Step 1c (kubectl config permissions)

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, the deploy failed at Step 1c with:

```
=== Step 1c: Ensuring kubectl config is accessible ===
chmod: cannot access '/etc/rancher/k3s/k3s.yaml': No such file or directory
```

Here is what happened, step by step:

1. The deploy script was run with the `--provision` flag, which tells Terraform to set up or update the AWS infrastructure. In this case, the Auto Scaling Group (ASG) had recently launched a brand-new spot instance to replace the previous one.

2. Once Terraform finished, the deploy script moved on to Step 1c. This step tries to change the file permissions on `/etc/rancher/k3s/k3s.yaml` — a configuration file that K3s (the lightweight Kubernetes system) creates when it is installed. The deploy needs this file to be readable so it can run `kubectl` commands against the cluster.

3. On this fresh spot instance, K3s had never been installed. The ASG launches new instances from a plain Ubuntu image with no software pre-installed. All the required tools (K3s, Docker, Helm, MariaDB, etc.) are installed by a separate one-time setup script called `bootstrap_ec2.sh`.

4. Because the deploy script assumed K3s was already present, it tried to change permissions on a file that did not exist, and the `chmod` command failed immediately. The `set -euo pipefail` safety setting at the top of the script then stopped the entire deploy.

5. The error message from `chmod` did not explain what to do about it. The deploy summary showed "No WARNING/ERROR keywords found" because the `chmod` error text does not contain either of those words — it just says "cannot access."

---

## What was changed

**`scripts/deploy/setup.sh`**

Added a check before the `chmod` command. The script now tests whether the K3s config file exists. If the file is missing, it prints a clear explanation of what happened and what to do — run the bootstrap script to set up the instance, then re-run the deploy.

```bash
# Before: assumed K3s was always installed — failed on fresh instances.
ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml"

# After: check first, give a clear error if K3s is not set up.
if ! ssh "$EC2_HOST" "test -f /etc/rancher/k3s/k3s.yaml"; then
    echo ""
    echo "ERROR: /etc/rancher/k3s/k3s.yaml not found on EC2."
    echo "  K3s is not installed on this instance. This happens when the ASG"
    echo "  launched a fresh spot instance that has not been set up yet."
    echo ""
    echo "  Run the bootstrap script first:"
    echo "    ./scripts/bootstrap_ec2.sh <ssh-host>"
    echo ""
    echo "  Then re-run this deploy."
    exit 1
fi
ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml"
```

If K3s is installed, the deploy proceeds exactly as before — the extra check adds less than a second.

---

## Why this didn't happen before

On the old long-running server (t3.large), K3s was installed once and stayed installed for the life of the instance. The config file was always present, so the `chmod` command always worked.

The spot instance Auto Scaling Group (introduced on 2026-04-13) replaces instances with a blank Ubuntu image whenever a spot interruption occurs. The launch template does not include any startup script to install K3s automatically — that is handled separately by `bootstrap_ec2.sh`. This gap means the deploy script can reach an instance where K3s has never been installed, which was not possible before the ASG was introduced.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/setup.sh` | Added existence check for `/etc/rancher/k3s/k3s.yaml` before the `chmod` — prints a clear error with instructions if K3s is not installed |
