# Incident: Deploy Stuck on "K3s Node Not Ready" — When the Node Was Ready All Along

**Date:** 2026-05-31
**Severity:** High for the deploy (two full deploys aborted at the very first cluster check, shipping nothing), but **no outage** — the live site stayed up the whole time.
**Affected components:** `scripts/deploy/common.sh` — `_wait_k3s_ready`, `_wait_k3s_api_ready`, and `_k3s_recover_and_diagnose` (the readiness-wait and recovery logic).

---

## Summary (the one-paragraph version)

My deploy kept failing at the start with "K3s node did not become Ready after 5/10 minutes." K3s is the software that runs all my app containers on the server — think of it as the server's "brain." Before loading new app versions, the deploy waits for that brain to say "I'm ready." The twist: **the brain *was* ready the entire time.** The wait step checks readiness by asking the cluster `kubectl get nodes`, but it asked using an ordinary login that doesn't have permission to read the cluster's config file (`/etc/rancher/k3s/k3s.yaml`, which is locked to the system administrator). So every check came back "permission denied," the script read that as "not ready," and it waited forever for a server that was already fine. The lock gets set whenever K3s restarts — and an earlier recovery step had restarted it. The fix is to do the readiness checks with `sudo k3s kubectl` (a built-in command that can always read the config), and to re-unlock the file after any restart.

---

## What happened

I ran the full deploy (`./scripts/deploy.sh`). Twice it failed early, after 7 and then 14 minutes, with:

```
✗ K3s node did not become Ready after 5 minutes — running diagnostic dump + hard recovery
✗ Hard recovery did not bring K3s Ready
```

The log was also flooded with:

```
pods "airflow-run-airflow-migrations-" is forbidden: error looking up service account
default/airflow-migrate-database-job: serviceaccount "airflow-migrate-database-job" not found
```

Those "service account not found" lines are **not the cause** — they're a harmless side effect of a background job retrying while setup isn't finished. They resolve on their own.

---

## How I found the real cause

I connected to the server (read-only) and checked the cluster's health two different ways — and they disagreed, which was the whole story.

**The deploy's own way (ordinary `kubectl`) failed:**
```
error: error loading config file "/etc/rancher/k3s/k3s.yaml": open /etc/rancher/k3s/k3s.yaml: permission denied
```

**The administrator's way (`sudo k3s kubectl`) showed a healthy node:**
```
NAME              STATUS   ROLES           AGE   VERSION
ip-172-31-64-52   Ready    control-plane   45m   v1.34.6+k3s1
```

So the node was **Ready** — the deploy just couldn't *see* it. I confirmed why: the config file was locked to the administrator only —

```
-rw-------  1 root root  /etc/rancher/k3s/k3s.yaml
```

The deploy's readiness check runs plain `kubectl get nodes` as an ordinary user, which can't read that locked file, so the check failed with "permission denied" on every single attempt. The script treated "I couldn't ask" the same as "the answer is no."

For the record, the server had plenty of headroom throughout (disk ~48%, 11 GB memory free, no resource pressure), and the only "out of memory" notes in the logs were from two days earlier and unrelated.

---

## Root cause

The readiness wait checked the cluster with **plain `kubectl`**, which depends on being able to read `/etc/rancher/k3s/k3s.yaml`. That file is created locked to the administrator (mode `600`), and **K3s re-locks it every time it restarts.** An earlier recovery step in a previous deploy had restarted K3s, leaving the file locked. From then on, every plain-`kubectl` readiness check returned "permission denied" — which the script misread as "node not ready" — so it waited out the entire window (even after I lengthened it to 10 minutes) and then failed. This is the same trap as the earlier [2026-04-17 kubectl-permission-denied incident](2026-04-17-mlflow-kubectl-permission-denied-k3s-restart.md): the deploy does unlock this file, but that unlock runs *later* than the readiness wait, so the very first cluster checks of the deploy were exposed.

A separate, secondary weakness made things worse: the wait used to **restart K3s after only 50 seconds** of "not ready." Since the node was actually fine, that restart did nothing useful — and a restart triggers a 1–3 minute internal-network rebuild — so it added churn. I fixed that too, but it was not the root cause.

---

## What I changed

All in `scripts/deploy/common.sh`. The core fix makes the readiness checks able to *see* the cluster no matter what; the rest is hardening.

1. **Check readiness with `sudo k3s kubectl`, not plain `kubectl`.** `sudo k3s kubectl` is K3s's own built-in command and always reads the config as the administrator, so a locked file can never again make a healthy node look "not ready." Applied to the node-ready check, the API-server check, and the diagnostic dump.

2. **Re-unlock the config file at the right moments.** The wait now unlocks `/etc/rancher/k3s/k3s.yaml` (sets it readable) *before* polling, and again *after* any restart it triggers — so the many later deploy steps that use plain `kubectl` also keep working.

3. **Stop restarting a node that's only slow.** Instead of an automatic restart after 50 seconds, the script now waits ~3 minutes and restarts **only if K3s is genuinely stuck** (its service is down or its container engine has frozen). A merely-slow node (CPU busy during the image build) is left alone to finish.

4. **More patience and a longer recovery window.** Node-ready wait 5 → 10 minutes; API-ready wait 6 → 8 minutes; the after-restart recovery re-check 90 seconds → 3 minutes (so the internal network has time to reconnect).

---

## Verification

- The edited script passes a syntax check (`bash -n scripts/deploy/common.sh`).
- **Live proof against the still-broken state:** with the config file still locked (`-rw-------`), the *new* readiness command (`sudo k3s kubectl get nodes | grep Ready`) correctly reported the node **Ready** — exactly where the old plain-`kubectl` command got "permission denied." The chmod pre-flight then unlocked the file (`-rw-r--r--`), and plain `kubectl get nodes` also showed `Ready`.
- So the next deploy should clear the readiness gate immediately instead of waiting on a check it could never pass.

---

## Lessons / notes

- **"Can't ask" is not the same as "the answer is no."** The deepest bug here was treating a *permission error* on the readiness check as *evidence the node was down*. A check that can fail to even run needs to tell those two cases apart — which is why the fix is to make the check always able to run (`sudo k3s kubectl`), not just to wait longer.
- **A restart silently re-locks the config file.** Any deploy step that runs after a possible K3s restart must assume `/etc/rancher/k3s/k3s.yaml` is locked again and either re-unlock it or use `sudo k3s kubectl`. This has now bitten the project twice (see the 2026-04-17 incident).
- **The scary log lines were a distraction.** The flood of "service account not found" errors looked alarming but were just a background job retrying; the real signal was the quiet "permission denied" on the node check.
- **Don't "fix" something that's only slow.** Restarting a healthy-but-busy node added a multi-minute network rebuild for no benefit. Recovery actions should be reserved for things that are actually broken.
- **If this happens again:** check the node yourself with `sudo k3s kubectl get nodes`. If it says `Ready`, the cluster is fine and the problem is in how the deploy is *looking* at it — start with the kubeconfig file permissions.
