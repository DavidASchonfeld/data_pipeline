# Incident: Two Parallel Deploy Jobs Fought Over the Same Image Store and One Deleted the Other's Half-Downloaded Image

**Date:** 2026-05-28
**Severity:** Low (the deploy finished successfully; the error was transient and a built-in retry recovered from it on its own — but it wasted time and printed an alarming line in the deploy summary)
**Status:** Resolved — I added a `flock` lock so the two jobs take turns using the shared image store

---

## Summary (the one-paragraph version)

After a full deploy printed `DEPLOY COMPLETE`, the "Warnings & Errors" section showed a scary-looking line: `Error response from daemon: failed commit on ref ... no such file or directory`. The deploy had actually worked — every service came up fine — so the question was why that error appeared at all. The cause: when I turn the AI features on (`GENAI_ENABLED=true`), the deploy now downloads two container images at the same time to save time — one for MLflow and one for the pgvector database. Both downloads use the **same** shared storage area on the server, and each download job also runs a "tidy up old images" cleanup step. The two jobs had no way to coordinate, so the pgvector job's cleanup ran at the exact moment the MLflow download was finishing and deleted a file MLflow was still writing — which produced the error. A built-in retry loop quietly re-downloaded it and the deploy carried on. To stop this from happening again I added a `flock` lock — think of it as a single "talking stick" that both jobs must hold before touching the shared storage. Only one job can hold it at a time, so a cleanup can never run while the other job is mid-download. The error can no longer occur, which means the deploy summary is clean because nothing actually goes wrong — not because I hid anything.

---

## What Happened

The deploy ran to completion in about 11.5 minutes and printed `DEPLOY COMPLETE`. But the summary at the end contained:

```
Error response from daemon: failed commit on ref "index-sha256:364c...":
commit failed: rename .../ingest/3b7d.../data .../blobs/sha256/364c...: no such file or directory
```

Reading through `/tmp/deploy-last.log` line by line told the whole story:

- **Line 1449** — the MLflow download starts: `Pulling MLflow image via Docker (attempt 1/3)...`
- **Line 1452-1453** — at the same time, the pgvector job runs its own cleanup step: `Pruning dangling Docker images to free disk space...`
- **Line 1454** — the MLflow download fails with the `failed commit on ref ... no such file or directory` error. The file it was about to finalize had just been deleted out from under it.
- **Line 1455** — the retry kicks in: `MLflow docker pull attempt 1 failed — removing partial image and retrying in 15s...`
- **Lines 1936-1937** — a later attempt succeeds: `Digest: sha256:364c...` / `Status: Downloaded newer image for ghcr.io/mlflow/mlflow:latest`
- **Lines 1969-1975** — the image is handed over to Kubernetes and confirmed present.

So MLflow ended up fine. The error was a one-off collision that the retry loop papered over — at the cost of a wasted download and a worrying line in the summary.

---

## Root Cause

To make deploys faster, the heavy work runs in parallel. When the AI features are switched on, three jobs run side by side: Kafka, MLflow, and pgvector (the database that stores text for the AI search). Two of those — MLflow and pgvector — download a container image using Docker.

Here is the key fact: **Docker keeps all downloaded images in one shared storage area on the server.** A download writes a temporary file into that area and then renames it into its final spot once complete. A "prune" (cleanup) sweeps that same area for leftover junk and deletes it.

Each download job, before it starts, runs a cleanup to free disk space. So while MLflow was finishing its download (renaming its temporary file into place), the pgvector job's cleanup swept the shared area and deleted that temporary file. The rename then had nothing to rename — hence `no such file or directory`.

The jobs had a partial guard already: Docker refuses to run two cleanups at the *exact* same instant (`prune operation is already running`). But that only protects cleanup-against-cleanup. It does nothing for the real problem here — a cleanup running against a download in progress. That gap is what bit me.

This problem is **new** with the AI work. Before pgvector joined the parallel group, MLflow was the only thing downloading in that window, so nothing ever raced against it. Now that two jobs download at once, the collision became possible — and it will keep happening on every full deploy with the AI features on until the jobs are made to coordinate.

---

## Fix

I gave the two jobs a shared lock using `flock`, a standard Linux tool for exactly this situation. `flock` points at a lock file — here `/tmp/docker-content-store.lock` — and lets only one process hold it at a time. Any other process that wants it waits until it is free.

Both jobs now wrap **every** command that touches the shared image store — the cleanups, the downloads, and the "delete the partial image" step that runs after a failed download — like this:

```bash
flock -w 600 /tmp/docker-content-store.lock docker pull <image>
```

Read aloud, that line means: *"Wait for the shared image store to be free (up to 10 minutes), take it, download the image, then release it for the other job."* Because both `mlflow.sh` and `pgvector.sh` point at the **same** lock file, they genuinely exclude each other — a cleanup in one job can never overlap a download in the other.

Changed files:

- **`scripts/deploy/mlflow.sh`** — wrapped the pre-download cleanup, the download itself, the failed-download cleanup, and the post-import cleanup in `flock`.
- **`scripts/deploy/pgvector.sh`** — same wraps, pointing at the same lock file.

A lock only helps if **every** mutation of the shared store goes through it. An unwrapped command could run while the other job holds the lock for its download and reopen the exact same gap — so the failed-download `docker rmi` is locked too, not just the obvious prune/pull.

Everything else in the two jobs (applying the Kubernetes manifests, waiting for the pods to come up) still runs fully in parallel. Only the brief moments of touching the shared image store now take turns.

---

## Why This Fix

**Why a lock at all?** This is a textbook case of two independent processes corrupting a shared resource. The standard answer is mutual exclusion — make them take turns. A lock is the simplest, most direct way to do that.

**Why `flock` specifically, and not a homemade lock?** A common do-it-yourself lock is to create a marker folder (`mkdir`) and treat its existence as "taken." The problem: if the deploy is interrupted — Ctrl-C, a dropped connection, a server hiccup — that marker is left behind and **blocks every future deploy** until someone deletes it by hand. `flock` ties the lock to the running process, so the moment the command ends (success, failure, or interruption) the lock is released automatically. No stale locks, no manual cleanup. It is also already installed on the server, so there was nothing extra to add.

**Why not just remove the cleanups from the parallel jobs instead?** That would also work, but it would mean holding both downloaded images on disk at once for longer. This project has a history of running low on disk during deploys, so I preferred the option that keeps the existing clean-as-you-go behavior and simply coordinates it.

**Why the 10-minute wait (`-w 600`)?** If one job is mid-download when the other wants the lock, the second job should *wait its turn*, not fail. A large image can take a few minutes, so 10 minutes is a comfortable ceiling. In practice the wait is short because downloads are limited by disk speed anyway — running them one after another barely changes the total time.

---

## Verification

After the next full deploy (`./scripts/deploy.sh` with `GENAI_ENABLED=true`):

1. **The summary is clean** — the "Warnings & Errors" section no longer contains `failed commit on ref`.
2. **No retries were needed** — in `/tmp/deploy-last.log`, both the MLflow and pgvector downloads succeed on `attempt 1/3`, with no `attempt 1 failed — retrying` line. This proves the collision was prevented, not just retried around.
3. **Both images land** — the log shows `Verifying image is visible to K3S...` for both MLflow and pgvector, and both pods reach `Running`.

A quick syntax check on the edited scripts before deploying:

```bash
bash -n scripts/deploy/mlflow.sh && bash -n scripts/deploy/pgvector.sh
```

---

## Lessons / Notes

- **`DEPLOY COMPLETE` with a clean ending can still hide a recovered error.** The summary scans the whole log for warning words, so a transient error that a retry quietly fixed still shows up there. A line in that section is worth tracing even when everything came up healthy.

- **Anything that runs in parallel and shares a resource needs to coordinate that resource.** The downloads were safe on their own; they only became unsafe once two of them shared the same storage area at the same time. When adding a new parallel job, the question to ask is: *does it touch anything the other jobs touch?*

- **Prefer `flock` over homemade locks in deploy scripts.** Deploys get interrupted (spot replacements, dropped SSH, Ctrl-C). A lock that survives an interruption will block the *next* deploy. `flock` releases itself when the process ends, which is exactly the behavior a deploy script wants.

- **If this happens again:** check whether a new download or cleanup step was added to the parallel phase without wrapping it in the same `flock /tmp/docker-content-store.lock`. An unwrapped Docker command in that phase reopens the gap.
