# Use official slim Python 3.12 image as base — slim reduces image size vs full python image
FROM python:3.12-slim

# Set working directory inside container; all subsequent paths are relative to /app
WORKDIR /app

# Create a non-root user and transfer /app ownership — follows least-privilege best practice for containers
RUN useradd -m appuser && chown appuser:appuser /app

# Copy requirements first (before app code) so Docker can cache this layer and skip reinstall when only app code changes
COPY --chown=appuser:appuser requirements.txt .

# Drop to non-root user before pip install — avoids "Running pip as root" warning and reduces attack surface
USER appuser

# pip installs executables to ~/.local/bin when running as a non-root user; add it to PATH so gunicorn and others are found at runtime
ENV PATH="/home/appuser/.local/bin:$PATH"

# Install Python dependencies; --no-cache-dir avoids storing pip cache in the image, keeping image size smaller
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app source code into /app
COPY --chown=appuser:appuser . .

# Declare that the container listens on port 5000 (documents intent; does not actually publish the port — that's done at runtime)
EXPOSE 5000

# app:app matches renamed flask entrypoint app.py
# --limit-request-line: rejects excessively long URLs — limits certain injection vectors
# --limit-request-fields: caps HTTP header count — mitigates header-injection amplification
# --workers=2: distributes load across 2 workers; --timeout=120: restarts worker on timeout instead of crashing
CMD ["gunicorn", "--workers=2", "--bind", "0.0.0.0:5000", "--timeout=120", "--limit-request-line", "4096", "--limit-request-fields", "100", "app:app"]
