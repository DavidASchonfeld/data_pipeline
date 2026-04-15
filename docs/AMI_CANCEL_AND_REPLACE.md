# AMI Cancel-and-Replace (April 14, 2026)

## What Is an AMI?

An AMI (Amazon Machine Image) is a saved snapshot of the pipeline server. Think of it like a
photograph of the server at a specific moment — it captures everything installed and configured
so the next time the server starts up, it can use this snapshot instead of setting up from
scratch. Using a saved snapshot, the server boots in **3–5 minutes** instead of **60 minutes**.

After every successful deploy, the system automatically creates a new snapshot in the background.
This takes about 15–25 minutes to complete.

---

## What Changed

Previously, if a snapshot was already being created when a new deploy finished, the system would
**skip** the new snapshot and let the old one finish. This created a problem:

1. You deploy version A of the code
2. A snapshot of version A starts baking in the background
3. You notice a bug in version A
4. You fix the bug and deploy version B
5. **Old behavior:** The snapshot of **buggy version A** keeps baking, and version B's snapshot
   is skipped entirely. The next cold boot uses the buggy snapshot.

**New behavior:** The system now **cancels** the in-progress snapshot and starts a fresh one
with the latest code. The newest deploy always wins.

---

## How It Works

1. **When a deploy finishes**, the system checks if a previous snapshot is still being created.
2. **If one is found**, it:
   - Stops the old snapshot process
   - Cancels the old snapshot in AWS (and deletes its storage to avoid unnecessary charges)
   - Confirms the server's services are still running properly
3. **Then it starts a fresh snapshot** of the server with the latest deployed code.

The snapshot runs silently in the background and does not block the deploy. A lock file
(`/tmp/ami-bake.lock`) tracks which process is running and which snapshot it is creating,
so each new deploy knows exactly what to cancel.

---

## Why This Was Added

- **Cost efficiency:** Cancelled snapshots and their storage are cleaned up immediately, so
  you are not paying for outdated server images sitting in AWS.
- **Correctness:** The server snapshot always reflects the most recently deployed code. There
  is no risk of the next cold boot using an outdated or buggy version.
- **Speed:** Developers do not have to wait for a previous snapshot to finish before deploying
  a fix. The fix deploys immediately and the new snapshot starts right away.

---

## Edge Cases Handled

| Situation | What happens |
|---|---|
| Server services were stopped mid-snapshot | The cancel process restarts them automatically |
| The old snapshot process already finished | The completed snapshot is cleaned up and replaced |
| The server is unreachable (sleeping) | Cancel proceeds gracefully — services will start on next boot |
| The old process ID was reused by the system | The cancel verifies the process is actually a snapshot before stopping it |
| The lock file is corrupted or empty | Each field is checked individually — missing values are safely skipped |

---

## Files Involved

- `scripts/deploy/ami.sh` — Contains the cancel and snapshot logic
- `scripts/deploy.sh` — Triggers the cancel-and-replace at the end of each deploy
