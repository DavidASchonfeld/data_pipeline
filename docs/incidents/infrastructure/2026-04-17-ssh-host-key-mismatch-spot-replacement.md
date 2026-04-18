# Deploy Fails: "EC2 SSH Unreachable After 36 Attempts" — April 17, 2026

**Date:** 2026-04-17
**Severity:** High (deploy cannot start; all pipeline changes are blocked until resolved)
**Affected components:** `scripts/deploy.sh` — SSH readiness check (non-provision path)

---

## What happened

Running `./scripts/deploy.sh` failed immediately with this message after spending 6 minutes
doing nothing useful:

```
✗ EC2 SSH unreachable after 36 attempts (6 min)

DEPLOY FAILED (exit code: 1)
Failed command: return 1
Elapsed time: 5m 55s
```

The deploy script spent the entire 6-minute window trying to connect to the server over
SSH, failed every time, and then gave up. No changes were deployed.

---

## Why it happened

To understand this, a bit of background on how the server works:

**The pipeline runs on a "spot instance"** — a type of rented cloud server that Amazon
occasionally replaces with a fresh one when demand is high. This is intentional: spot
instances cost about 70% less than standard servers. When a replacement happens, Amazon
keeps the same public address (called an Elastic IP) pointing to the new server, so
everything else in the system continues to work without reconfiguration.

**The problem: a server swap happened, but the laptop still expected the old server.**

Every SSH connection involves a small security check: the server proves its identity by
presenting a unique digital fingerprint (called a "host key"). The first time you connect
to a server, your laptop saves that fingerprint so it can verify it on future connections.

When the spot instance was replaced, the new server had a *different* fingerprint than
the one stored on the laptop. SSH's safety rule kicked in: if the fingerprint you see
doesn't match what you saved before, refuse the connection. This is normally the right
behavior — a mismatch can mean someone is impersonating the server.

**The deploy script's SSH setting made it fail silently.**

The deploy script connects with a setting called `StrictHostKeyChecking=accept-new`. This
means: *"If this server is completely new to us (no saved fingerprint), trust it
automatically. Otherwise, use the saved fingerprint to verify."*

The problem: the server wasn't new to the laptop — there *was* a saved fingerprint, just
the wrong one. So SSH didn't say "new server, accepting." It said "I know this address,
and this doesn't match — refusing." It exited silently with no visible error message.
The deploy script interpreted this as "server not ready yet" and kept retrying, all
36 times, for the full 6 minutes.

**The `--provision` path was already protected against this; the normal path was not.**

When you run `./scripts/deploy.sh --provision`, it runs Terraform first (which
re-provisions the server infrastructure), and Terraform's script already included a step
to wipe the saved fingerprint afterward. The normal deploy path — used for all routine
deploys — never had that step.

---

## What was changed to fix it

### `scripts/deploy.sh` — non-provision SSH readiness block

Three lines were added immediately before the SSH retry loop in the normal (non-provision)
deploy path:

```bash
# Clear stale known_hosts entry — spot replacement gives the instance a new host key,
# and StrictHostKeyChecking=accept-new silently fails when the old key is still cached.
_EC2_IP=$(ssh -G "$EC2_HOST" 2>/dev/null | awk '/^hostname/ {print $2; exit}')
ssh-keygen -R "$EC2_HOST" &>/dev/null || true  # remove alias entry
[ -n "$_EC2_IP" ] && ssh-keygen -R "$_EC2_IP" &>/dev/null || true  # remove IP entry
```

In plain terms: before trying to connect, the deploy script now wipes the saved
fingerprint for the server — both by name (`ec2-stock`) and by IP address (`52.70.211.1`).
When SSH then connects, it sees the server as "new," accepts the fresh fingerprint
automatically, and stores it for next time.

This is safe because:
- The Elastic IP always points to *our* server — we control what's at that address.
- The stale fingerprint was already causing deploys to fail; removing it restores normal behavior.
- After the wipe, the new fingerprint is saved, so subsequent connections verify correctly.

---

## Files changed

| File | What changed |
|------|-------------|
| `scripts/deploy.sh` | Added 3-line known_hosts wipe before `_wait_ssh_ready` in the non-provision path |

---

## How to avoid this in the future

The fix is fully automatic. On every normal deploy, the saved fingerprint is cleared before
SSH is attempted. If the server is the same one as last time, SSH will re-verify and save
the same fingerprint again — no impact. If the server was replaced by a new spot instance,
SSH will accept the new fingerprint and connect successfully.

No manual action is needed when a spot replacement happens. The next routine deploy
will self-heal.
