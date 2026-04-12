# Flask Pod Not Ready: Missing PATH for appuser — April 12, 2026

## What Happened

After fixing the "pip running as root" warning in `dashboard/Dockerfile` (by switching to `appuser` before running `pip install`), a new failure appeared on the next deploy: the Flask pod never reached Ready state within the 90-second window. The deploy script printed:

```
WARNING: Flask pod did not become Ready within 90s. Current state:
```

During the Docker build step, several warnings also appeared:

```
WARNING: The script gunicorn is installed in '/home/appuser/.local/bin' which is not on PATH.
WARNING: The script flask is installed in '/home/appuser/.local/bin' which is not on PATH.
... (similar for dotenv, pygmentize, etc.)
```

These warnings were the leading indicator of the actual failure.

## Root Cause

When pip runs as a non-root user (without a virtual environment), it installs executables to `~/.local/bin` — in this case `/home/appuser/.local/bin`. The container's `PATH` environment variable did not include that directory.

The Dockerfile uses the exec form of CMD:

```dockerfile
CMD ["gunicorn", "--workers=2", "--bind", "0.0.0.0:5000", ...]
```

Exec form does not invoke a shell — it looks up `gunicorn` directly via `PATH`. Since `/home/appuser/.local/bin` was not in `PATH`, Kubernetes could not find `gunicorn` when starting the container. The container exited immediately with a "not found" error, entered CrashLoopBackOff, and never passed the readiness probe.

## Why It Wasn't Caught Sooner

The `docker build` step itself succeeded (pip install completed normally). The failure only happens at container startup, when Kubernetes tries to execute the CMD. The PATH mismatch warnings during build were the hint, but they don't cause the build to fail.

## Fix

Added one line to `dashboard/Dockerfile`, immediately after `USER appuser` and before the `pip install`:

```dockerfile
ENV PATH="/home/appuser/.local/bin:$PATH"
```

This ensures:
- The PATH is set correctly for the `pip install` step (no more warnings)
- The PATH is baked into the image and present at container runtime
- `gunicorn` resolves correctly when Kubernetes starts the pod
- The Flask pod becomes Ready within the 90-second window

## Files Changed

- `dashboard/Dockerfile` — added one `ENV PATH` line (line 15)

## How to Verify

After the next `./scripts/deploy.sh`:
1. Docker build output shows no "not on PATH" warnings
2. The "Flask pod did not become Ready" warning is gone
3. `kubectl get pods -n default` shows Flask pod as `Running` with `Ready 1/1`
