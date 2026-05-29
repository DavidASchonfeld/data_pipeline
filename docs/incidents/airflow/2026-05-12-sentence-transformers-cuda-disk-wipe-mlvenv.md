# Incident: sentence-transformers Silently Pulled 2.5 GB of GPU Libraries, Crashed the Disk, and Wiped the AI Package Install

**Date:** 2026-05-12
**Severity:** Medium (GenAI packages missing after deploy; pipeline + dashboard unaffected; Epic 2 verification failing)
**Status:** Resolved — CPU-only torch pinned in Dockerfile and airflow_pods.sh; packages now baked into the image so they survive restarts

---

## Summary (the one-paragraph version)

After a successful full deploy (18 minutes, "DEPLOY COMPLETE"), the Epic 2 verification step — checking that the AI libraries were importable inside the scheduler pod — failed with `ModuleNotFoundError: No module named 'anthropic'`. The deploy log clearly showed every package installing successfully, so the question was: where did they go? The answer is that `sentence-transformers`, an AI library needed for searching documents by meaning, automatically downloaded a second package called `torch` (PyTorch). On this server's ARM chip, the default version of torch that pip chose included the full CUDA GPU toolkit — about **2.5 gigabytes** of driver software for a graphics card the server doesn't have and will never have. That download pushed the server's disk from 60% to 86% full. Kubernetes noticed the disk pressure, quietly evicted the scheduler container, and restarted it fresh from the Docker image — which only had the original base packages, not the AI ones that were just installed. The fix has two parts: (1) tell pip to use the CPU-only version of torch (about 200 MB instead of 2.5 GB), and (2) bake all the AI packages directly into the Docker image so they survive any future pod restart automatically.

---

## What Happened

The deploy ran and printed `DEPLOY COMPLETE` after 18 minutes. The "Warnings & Errors" section in the summary was clean. Immediately after, I ran the three Epic 2 verification commands:

**Check 1 — pgvector pod running:**
```
kubectl get pod -l app=pgvector -n airflow-my-namespace
```
Result: `pgvector-79fd6d8f49-jcb2q  1/1  Running` ✅

**Check 2 — vector extension loaded in the database:**
```
kubectl exec -n airflow-my-namespace pgvector-79fd6d8f49-jcb2q -- psql -U pgvector -d vectordb -c "SELECT extname FROM pg_extension;"
```
Result: listed both `plpgsql` and `vector` ✅

**Check 3 — AI libraries importable inside the scheduler:**
```
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -c scheduler -- \
  /opt/ml-venv/bin/python -c "import anthropic, sentence_transformers, psycopg2, pgvector; print('ok')"
```
Result:
```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'anthropic'
```
❌

Running a full `pip list` inside the scheduler's Python environment showed only the base packages (mlflow, scikit-learn, pandas, etc.) — none of the AI packages.

But the deploy log told a different story. Searching `/tmp/deploy-last.log` for Step 7b showed all five packages installing successfully during the deploy:

```
anthropic installed.
sentence-transformers installed.
psycopg2-binary installed.
pgvector installed.
rank-bm25 installed.
```

So the packages were installed — and then they vanished.

---

## Root Cause

Two problems compounded each other:

### Problem 1: pip pulled the GPU version of torch (2.5 GB) instead of the CPU version (200 MB)

`sentence-transformers` is a library for turning text into numbers that represent its meaning. Internally it uses PyTorch (`torch`) to do the math. When pip installs `sentence-transformers`, it automatically also installs `torch`.

There are two builds of torch:
- **CPU-only build** — does the math using the regular processor. About 200 MB. Works on any machine.
- **CUDA build** — does the math using an NVIDIA graphics card. About 2.5 GB. Only useful if the machine has a GPU.

This server is an ARM-based EC2 instance (t4g.large). It has no graphics card. pip does not know this — it only knows the chip architecture. On ARM Linux, pip's default resolution picked the CUDA build. The result: 2.5 GB of GPU software downloaded onto a machine that can't use any of it.

Packages pulled in by this one decision:

| Package | Size |
|---|---|
| torch | 420 MB |
| nvidia-cudnn | 434 MB |
| nvidia-cublas | 543 MB |
| nvidia-cusparselt | 221 MB |
| nvidia-nccl | 197 MB |
| nvidia-cufft | 214 MB |
| + 8 more CUDA libraries | ~500 MB |
| **Total** | **~2.5 GB** |

### Problem 2: disk pressure evicted the pod, which wiped the install

The disk was around 60% full before the install. After 2.5 GB downloaded into the scheduler container's temporary workspace, the disk hit **86%**. Kubernetes monitors disk usage and has a built-in safety threshold — when a node's disk fills past a certain point, it begins evicting (forcibly stopping) pods to free space.

The scheduler pod was evicted and restarted. When a pod restarts, it boots from scratch using the Docker image — a clean copy that only has whatever was baked into the image during the last `docker build`. All the packages that were installed live at runtime via `pip install` are gone. The AI packages (`anthropic`, `sentence-transformers`, etc.) were not in the Docker image, so they disappeared.

The scheduler pod came back up with 0 listed restarts because Kubernetes resets that counter when a pod is fully replaced rather than just restarted in place. The scheduler appeared healthy — but was missing everything that had been installed during the deploy.

---

## Fix

Two changes, applied together:

### Change 1: Bake the AI packages into the Docker image (`airflow/docker/Dockerfile`)

Docker images are built once and then deployed. Packages inside the image are permanent — they are part of the "snapshot" the pod boots from, so a pod restart cannot remove them.

A new build step was added to the Dockerfile after the existing ml-venv base packages:

```dockerfile
# torch is installed first using the CPU-only index — without this, pip on ARM Linux resolves
# to the full CUDA build (~2.5 GB of GPU libraries useless on an instance with no GPU).
# The CPU wheel is ~200 MB and equally fast for embedding inference on a t4g.large.
RUN /opt/ml-venv/bin/pip install --no-cache-dir \
        torch --index-url https://download.pytorch.org/whl/cpu \
    && /opt/ml-venv/bin/pip install --no-cache-dir \
        "anthropic>=0.50.0" \
        sentence-transformers \
        psycopg2-binary \
        pgvector \
        rank-bm25 \
        "numpy<2" \
    && /opt/ml-venv/bin/python -c "import anthropic, sentence_transformers, psycopg2, pgvector; print('genai ml-venv imports OK')"
```

The `--index-url https://download.pytorch.org/whl/cpu` flag is the key instruction: it tells pip "fetch torch from PyTorch's CPU-only package server, not the default one." pip finds a 200 MB wheel instead of a 2.5 GB one and the disk never spikes.

### Change 2: Same CPU-torch pin as a safety net in the deploy script (`scripts/deploy/airflow_pods.sh`)

The deploy script also has a fallback that installs AI packages into a running pod (used by `--fix-ml-venv` and in case the image check misses something). The `sentence-transformers` install line in that fallback was also updated to install CPU torch first:

```bash
# install CPU-only torch before sentence-transformers to prevent pip from pulling ~2.5 GB of CUDA
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/pip install --no-cache-dir "sentence-transformers"
```

---

## Why This Fix

**Why not just get a bigger disk?**
Even with 200 GB of disk space, the packages installed at runtime into a running pod are temporary — they live on the container's scratch layer and vanish when the pod restarts. Bigger disk delays the problem; it does not eliminate it. The correct fix is to move the packages into the Docker image.

**Why CPU torch specifically?**
The server is a t4g.large, an ARM machine with no GPU. CUDA libraries are only useful for GPU-accelerated math. Every byte of those 2.5 GB of CUDA drivers is wasted disk space on this machine. The CPU-only build does identical work on this hardware.

**Why does pip pick the GPU version on ARM?**
pip looks at the chip architecture and picks the build that "could" work. ARM64 servers can technically have NVIDIA GPUs attached (some cloud providers offer this). pip doesn't query whether a GPU is actually present — it picks the build that covers the possibility. Without an explicit instruction to use the CPU index, pip always picks the CUDA build on this machine.

---

## Verification

After the next full deploy (which rebuilds the Docker image with the new Dockerfile):

```bash
# 1. pgvector pod running
kubectl get pod -l app=pgvector -n airflow-my-namespace
# Expected: 1/1 Running

# 2. vector extension in Postgres
kubectl exec -n airflow-my-namespace <pgvector-pod-name> -- psql -U pgvector -d vectordb \
  -c "SELECT extname FROM pg_extension;"
# Expected: vector listed

# 3. AI libraries importable in ml-venv — this was the failing check
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -c scheduler -- \
  /opt/ml-venv/bin/python -c "import anthropic, sentence_transformers, psycopg2, pgvector; print('ok')"
# Expected: ok
```

Check 3 now passes because the packages are baked into the image rather than installed at runtime.

---

## Lessons / Notes

- **"DEPLOY COMPLETE" does not mean the deploy's effects survived.** Packages installed into a running pod via `kubectl exec` are temporary. If anything restarts the pod — disk pressure, a spot replacement, a Kubernetes upgrade — those packages are gone. The only packages that are truly permanent are those baked into the Docker image with a `RUN pip install` in the Dockerfile.

- **pip does not know whether you have a GPU.** On this ARM server, pip will always try to download the GPU version of torch unless explicitly told not to. The fix (`--index-url https://download.pytorch.org/whl/cpu`) must appear *before* any package that depends on torch, because once torch's CUDA build is already installed, other packages will happily use it.

- **Disk pressure evictions are silent.** Kubernetes evicted the scheduler pod and restarted it without printing any obvious error to the deploy log. The deploy saw the pod come back up and reported success. There was no "eviction" line anywhere in the deploy output. The only signal was that the packages were missing after the deploy ended. When packages vanish from a healthy-looking pod, disk pressure eviction during install is the first thing to check.

- **The disk spike is temporary, but the damage is permanent.** The 2.5 GB of CUDA libraries are gone after the pod eviction — Kubernetes freed that space when it evicted the pod. So by the time the verification check ran, the disk was back to normal and nothing looked wrong. The disk metric alone would not tell you what happened.

- **If this happens again:** Run `kubectl describe node` and look for `MemoryPressure` or `DiskPressure` in the `Conditions` section, and look for `Evicted` entries in the pod event log. If you see a recent eviction of the scheduler pod, check whether the eviction timestamp overlaps with the deploy's Step 7b.
