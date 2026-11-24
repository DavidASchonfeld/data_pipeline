# Builds the FastAPI app. Today it only answers a health check, which proves the local loop works end to
# end (package installs, container starts, request answered); search routes are added here later.
# Run locally with: uvicorn agent_data.engine.service.api.app:create_app --factory

from __future__ import annotations

from agent_data.engine._shared.errors import require_extra

# The service name reported by the health check, so a caller can tell which API answered.
SERVICE_NAME = "agent-data-api"


def health() -> dict[str, str]:
    # Liveness answer: if this returns, the process is up and able to serve requests.
    return {"status": "ok", "service": SERVICE_NAME}


def create_app():  # -> fastapi.FastAPI (not annotated so importing this module never requires fastapi)
    # Create the app with its routes; fastapi is an optional extra, so a missing install fails with a clear hint.
    fastapi = require_extra("fastapi", "api")
    app = fastapi.FastAPI(title=SERVICE_NAME, docs_url="/docs", redoc_url=None)
    app.add_api_route("/healthz", health, methods=["GET"], tags=["ops"])
    return app
