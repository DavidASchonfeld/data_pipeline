# Dashboard Not Publicly Reachable After Deploy — April 15, 2026

**Date:** 2026-04-15
**Severity:** Low (brief outage during deploy, self-resolved)
**Affected components:** Dashboard public access (port 32147)

---

## What happened

After a successful deploy (`./scripts/deploy.sh`, 7 minutes, no errors), the dashboard at `http://52.70.211.1:32147/dashboard/` could not be reached from a web browser. Safari displayed "cannot connect to the server."

A few minutes later, the dashboard was reachable again without any manual intervention.

---

## Why it happened

During every deploy, the Flask pod (the container that runs the dashboard) is deleted and recreated with the new code. This is step 6 in the deploy process. While the old pod is shutting down and the new one is starting up, there is a window — typically 30 to 90 seconds — where nothing is serving on port 32147.

The deploy script does wait for the new pod to become healthy before printing "DEPLOY COMPLETE." However, the dashboard URL was checked in the browser around the same time the pod was restarting. Because the deploy runs Flask in the background while other steps continue, the timing of when the pod is down versus when the user checks the browser can overlap.

Two other factors made this harder to notice and diagnose:

1. **The deploy script only checked internal health, not public reachability.** It asked Kubernetes "is the pod healthy?" (which checks inside the server) but never tested whether the dashboard was actually reachable from the internet. A pod can be healthy inside Kubernetes but still unreachable from outside if the firewall, network, or DNS is misconfigured.

2. **The deploy printed `http://localhost:32147/dashboard/` as the verification URL.** This is the SSH tunnel URL, not the public URL. The actual public URL is `http://52.70.211.1:32147/dashboard/`. Seeing the wrong URL at the end of a deploy makes it easy to assume public access is not expected to work.

---

## What was done to fix it

### 1. Confirmed the dashboard was reachable

Ran `curl http://52.70.211.1:32147/health` from the local machine — returned HTTP 200. Ran `terraform apply` to confirm the AWS firewall (Security Group) already had port 32147 open to the public. No infrastructure changes were needed.

### 2. Added a public connectivity check to deploys

The deploy script (`scripts/deploy/flask.sh`) now tests whether the dashboard is reachable from the outside after confirming the pod is healthy. If the page is not reachable, it prints a warning with instructions:

```
WARNING: Flask pod is Ready but not publicly reachable at http://52.70.211.1:32147/
  The AWS Security Group may not allow inbound traffic on port 32147.
  Fix: ./scripts/deploy.sh --provision
```

This catches genuine access problems (misconfigured firewall, networking issues) automatically on every future deploy, instead of leaving it as a silent failure.

### 3. Updated deploy output

The deploy script previously printed `http://localhost:32147/dashboard/` as the verification URL, which only works through an SSH tunnel. It now prints the actual public URL so the correct link is visible at the end of every deploy.

The outdated access note at the bottom of `deploy.sh` (which said ports 30080 and 32147 were "probably blocked") was also updated to reflect that port 32147 is intentionally open to the public.

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy/flask.sh` | Added a public connectivity check after the pod health verification |
| `scripts/deploy.sh` | Updated the printed dashboard URL and replaced the outdated access note |

---

## How to avoid this in the future

The dashboard will briefly go offline during every deploy (while the pod restarts). This is expected and lasts under 90 seconds. If the dashboard stays offline for longer than that, the new connectivity check will flag it in the deploy output.

If the connectivity check warns that the dashboard is not publicly reachable, the most likely cause is a firewall issue. Run the deploy with the `--provision` flag to sync the firewall rules:

```
./scripts/deploy.sh --provision
```

A plain `./scripts/deploy.sh` only deploys code — it does not touch infrastructure like firewall rules. The `--provision` flag runs Terraform first, which applies any pending infrastructure changes.
