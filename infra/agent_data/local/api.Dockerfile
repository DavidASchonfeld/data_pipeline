# Local image for the agent_data API (used by docker-compose.yml). Only pyproject.toml and agent_data/ are
# copied in (see api.Dockerfile.dockerignore), and everything runs as a non-root user, pip included.
FROM python:3.12-slim

# Don't write .pyc files into the image, and print logs immediately instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Create an unprivileged user and a virtualenv it owns, so pip never runs as root.
RUN useradd --create-home --uid 10001 appuser \
    && python -m venv /opt/venv \
    && chown -R appuser:appuser /opt/venv

USER appuser
WORKDIR /app

# Copy the package definition and code, then install it with only the API extra.
COPY --chown=appuser:appuser pyproject.toml ./
COPY --chown=appuser:appuser agent_data ./agent_data
RUN pip install --no-cache-dir ".[api]"

EXPOSE 8000
CMD ["uvicorn", "agent_data.engine.service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
