# Incident: MLflow's `:latest` Image Drifted to a Newer DB Schema and CrashLoopBackOff'd the Whole Deploy

**Date:** 2026-06-02
**Severity:** Medium (no data loss and the public website stayed up, but it blocked every deploy until fixed — including an unrelated feature I was trying to ship)
**Status:** Resolved — MLflow server **and** client pinned to the same exact version (`v3.13.0`, no more `:latest`); the container migrates its own DB forward on startup (guarded so a fresh DB isn't broken); and the one deprecated MLflow call in the anomaly detector was updated. The whole fix was validated locally before deploying.

---

## Summary (the one-paragraph version)

I was redeploying to ship a new feature when the deploy failed partway through — not on my feature, but on **MLflow**, the experiment-tracking service. The MLflow pod was stuck in `CrashLoopBackOff` with `Detected out-of-date database schema (found version 7d34483879f0, but expected da6fb0208061)`. I hadn't touched MLflow. The cause was that my MLflow container was pulled from the floating `:latest` tag, so a newer MLflow release silently became the running version; it expected a newer database layout than the one already on disk, and MLflow refuses to start against a DB it considers out of date — it makes you migrate on purpose. A component I never changed broke a deploy of a component it has nothing to do with. The fix: stop using `:latest` (pin to an exact version), make the container migrate its own database on startup, keep the client and server on the same version, and remove the one deprecated API call this surfaced.

---

## Background: what was actually broken

MLflow stores everything in a single **SQLite database file** on a persistent disk (`/mlflow-data/mlflow.db`). That database has a **schema** stamped with an alembic revision id (the cryptic strings like `7d34483879f0`). On startup MLflow compares the on-disk revision against the revision its own code expects; if the disk is older it stops and tells you to migrate — it will **not** silently change your database.

Two things conspired:

1. **The image was `ghcr.io/mlflow/mlflow:latest`.** `:latest` is not a fixed version — it's "whatever was published most recently." The running MLflow could (and did) move forward every time a deploy re-pulled the image, with no change on my side, until it pulled a release whose expected schema was newer than the on-disk one.
2. **The deploy waits for MLflow before continuing** (`_wait_bg $MLFLOW_PID`). A broken MLflow therefore blocks *everything downstream of it in the same deploy* — the dashboard, the pipeline, the feature I was shipping.

The real lesson: a **non-critical** component on a **floating tag** can **block deploys of critical components**, at a time I didn't choose, triggered by someone else's release schedule.

---

## Root cause

Using a floating image tag (`:latest`) for a stateful service. The running version moved forward underneath me with no code change, and crossed a database-schema boundary that MLflow (correctly) refuses to cross automatically. A secondary contributor: nothing migrated the database, so the first version bump that touched the schema was guaranteed to crash rather than recover.

---

## The fix (and one wrong turn worth recording)

**Wrong turn — pinning *backward* to v2.15.1.** My first instinct was to pin to `v2.15.1` (the version of the mlflow *client* in the ml-venv). That failed differently: `alembic.util.exc.CommandError: Can't locate revision identified by '7d34483879f0'`. The on-disk DB had already been migrated to a **MLflow 3.x** schema by the previous `:latest`, and MLflow cannot **downgrade** a database — a 2.x server simply doesn't know a 3.x revision. The DB being 3.x is a fait accompli; the fix is to go *forward*, not back. (Two other 3.x-isms confirmed this direction: the manifest's `--allowed-hosts` flag doesn't exist in 2.x, and 2.x couldn't open the 3.x DB at all.)

**The actual fix — pin *forward* to a single exact 3.x version, server and client together:**

1. **Pin server + client to `v3.13.0`.** Three places, all the same version:
   - `airflow/manifests/mlflow/deployment-mlflow.yaml` — `image: ghcr.io/mlflow/mlflow:v3.13.0`
   - `scripts/deploy/mlflow.sh` — `MLFLOW_IMAGE="ghcr.io/mlflow/mlflow:v3.13.0"`
   - `airflow/docker/Dockerfile` — ml-venv `mlflow==3.13.0` (was 2.15.1) — ends the client/server version skew.

   v3.13.0 is effectively the version `:latest` had already become (its schema head is `da6fb0208061`, the exact revision the crash said it "expected"), so this keeps the existing DB and **all run history** and just migrates one step forward. **Tag gotcha:** ghcr.io MLflow tags carry a `v` prefix — `v3.13.0`, not `3.13.0` (a plain `3.13.0` returns "not found", which cost a deploy).

   **Transitive-dependency fallout (the image build's first failure):** bumping the ml-venv client to `mlflow==3.13.0` broke the Docker build with `ResolutionImpossible` — `mlflow 3.13.0` requires `cryptography>=43`, but the pinned `snowflake-connector-python==3.10.1` requires `cryptography<43`. The two can't share the venv. Fix: bump the ml-venv connector to `snowflake-connector-python==3.18.0` (newest 3.x — same major, lower risk than 4.x), which allows `cryptography>=43`. The dashboard image keeps `3.10.1` (it has no MLflow, so no conflict). Verified locally that the full ml-venv block installs and imports, the RSA key-pair auth path still works under cryptography 46, and the genai block's `numpy<2` still coexists (mlflow's pyarrow 24 imposes no numpy pin).

2. **Migrate the DB on startup, guarded.** The container command now runs:

   ```
   DB=/mlflow-data/mlflow.db; if [ -f "$DB" ]; then mlflow db upgrade "sqlite:///$DB"; fi; exec mlflow server …
   ```

   `mlflow db upgrade` moves the schema forward (non-destructive — history preserved) and is a no-op when already current. The `[ -f "$DB" ]` **guard matters**: `mlflow db upgrade` on a brand-new/empty file errors with `no such table: metrics` (it expects the base tables MLflow normally creates via `create_all`), so on first boot we let `mlflow server` initialize the DB itself and only upgrade an existing one. `exec` makes `mlflow server` PID 1 so it still receives K8s stop signals.

3. **Remove the deprecated API call.** MLflow 3.x renamed `mlflow.sklearn.log_model(model, "isolation_forest", …)`'s positional `artifact_path` argument to the keyword `name` and deprecates the old form. `airflow/dags/anomaly_detector.py` now calls `log_model(model, name="isolation_forest", …)` — no deprecation warning, forward-compatible.

---

## Validated locally before deploying (so the 15-minute deploy wasn't the test)

Each MLflow deploy takes ~15 minutes to fail, so I reproduced the relevant pieces on my laptop with `pip install mlflow==3.13.0` first:

- The `ghcr.io/mlflow/mlflow:v3.13.0` image tag exists (registry probe).
- v3.13.0's `mlflow server` accepts `--allowed-hosts` (a 3.x-only flag the manifest uses).
- v3.13.0's migrations **contain** revision `7d34483879f0`, so `db upgrade` can read the on-disk DB (no "Can't locate revision").
- **Faithful migration test:** built a SQLite DB, rolled it back to exactly `7d34483879f0` (matching the in-cluster DB), then ran `mlflow db upgrade` under v3.13.0 → exit 0, migrated forward to `da6fb0208061`.
- `mlflow==3.13.0` resolves cleanly with the ml-venv's pinned `numpy<2` (it declares `numpy<3`), `scikit-learn==1.5.2`, `pandas==2.2.2` — so bumping the client doesn't disturb the genai layer's `numpy<2` requirement.
- `log_model(name="isolation_forest", …)` runs under 3.13.0 + numpy 1.x with **zero** deprecation warnings; the old positional form logs the `artifact_path` deprecation.
- (Aside, doesn't affect us) MLflow 3.x disabled the local *file-store* backend; the production server uses SQLite, so it's unaffected.

---

## How to bump MLflow on purpose (the intended workflow)

Pinning is not "never update" — it's "update when I choose, not when a release happens." To move to a newer MLflow:

1. Change the tag in **all three** places to the same version: `scripts/deploy/mlflow.sh`, `airflow/manifests/mlflow/deployment-mlflow.yaml`, and the `mlflow==` pin in `airflow/docker/Dockerfile`'s ml-venv. (ghcr tags carry the `v` prefix; the pip pin does not.)
2. Redeploy. The guarded `mlflow db upgrade` migrates the SQLite schema forward automatically.
3. Confirm the MLflow pod reaches `Running` and the UI loads. If anything's wrong, revert the tag — because the version is now exact, rollback is a one-line change.

---

## Prevention / lessons

- **Pin versions for anything stateful or anything a deploy blocks on.** Floating tags trade reproducibility for "freshness I didn't ask for." A deploy that worked yesterday should work today with no code change; `:latest` breaks that guarantee.
- **Keep client and server on the same version.** Half this mess was the server floating to 3.x while the client stayed pinned at 2.15.1.
- **Migrate persistent stores on startup, but guard the empty-DB case.** Data outlives the container, so the container must bring the data up to its own expectations — but the migration tool assumed an existing DB, so the first boot must initialize instead.
- **A non-critical service shouldn't be able to block the whole deploy** (future work: make the MLflow wait non-fatal so a broken tracker can't stop the dashboard/pipeline from shipping).
- **When each deploy costs 15 minutes, reproduce the failure locally first.** Pinning the exact version on a laptop and replaying the migration caught three would-be-failed deploys (wrong tag format, the downgrade dead-end, the empty-DB guard) before they cost real time.
