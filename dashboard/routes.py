import logging
import pandas as pd
from flask import Flask
from sqlalchemy import text

from db import DB_ENGINE, _prewarm_event  # _prewarm_event signals when cache is fully warm
from security import limiter, require_basic_auth  # rate limiting decorators and basic-auth helper
from spot import check_spot_interruption  # spot termination polling — safe no-op on non-EC2

logger = logging.getLogger(__name__)  # module-level logger — writes to pod stdout (visible in kubectl logs)


def register_routes(app: Flask) -> None:
    """Register all Flask routes onto the given app instance."""

    @app.route('/')
    @app.route('/index')
    @limiter.limit("30 per minute")  # static redirect — cheap but worth capping per IP
    def index():
        # Redirect root to the Dash dashboard
        return (
            '<h2>Stock Market Analytics Pipeline</h2>'
            '<p>Visit <a href="/dashboard/">the dashboard</a> to see live stock charts.</p>'
        )

    @app.route('/health')
    @limiter.exempt  # K8s liveness/readiness probes hit this every 5-10s — must never be rate-limited
    def health():
        # Health-check endpoint — useful for Kubernetes liveness probes
        # No DB call needed; fast, reliable signal that pod process is running
        return {"status": "ok"}, 200

    @app.route('/health/ready')
    @limiter.exempt  # polled during startup and spot recovery — must never be rate-limited
    def health_ready():
        # Returns 200 only after prewarm_cache() has finished all Snowflake queries
        # Wake Lambda uses this instead of /health so it redirects only when data is cached
        if _prewarm_event.is_set():
            return {"status": "ok"}, 200
        return {"status": "warming"}, 503

    @app.route('/api/spot-status')
    @limiter.exempt  # monitoring endpoint — same pattern as /health, never rate-limit
    def spot_status():
        # Returns current spot interruption state; no sensitive data exposed
        return check_spot_interruption(), 200

    @app.route('/validation')
    @limiter.limit("5 per minute")  # debug endpoint — tight cap; infrequent legitimate use only
    @require_basic_auth              # blocks unauthenticated requests before any DB query runs
    def validation():
        # Data validation endpoint — shows table schemas, row counts, and freshness
        # Used for monitoring: detect when DAGs fail or data stops flowing
        try:
            validation_info = {
                "status": "ok",
                # Include timestamp so caller knows when data was sampled
                "timestamp": pd.Timestamp.now().isoformat(),
                "tables": {}
            }

            with DB_ENGINE.connect() as conn:
                # Validate company_financials table (SEC EDGAR data written by dag_stocks.py)
                stock_count  = conn.execute(text("SELECT COUNT(*) FROM company_financials")).scalar()
                stock_latest = conn.execute(text("SELECT MAX(period_end) FROM company_financials")).scalar()
                stock_sample = pd.read_sql(
                    text("SELECT * FROM company_financials ORDER BY period_end DESC LIMIT 5"),
                    conn
                )
                validation_info["tables"]["company_financials"] = {
                    "row_count": int(stock_count),
                    "latest_period_end": str(stock_latest),
                    "sample_data": stock_sample.to_dict('records') if len(stock_sample) > 0 else []
                }

                # Validate weather_hourly table
                weather_count  = conn.execute(text("SELECT COUNT(*) FROM weather_hourly")).scalar()
                weather_latest = conn.execute(text("SELECT MAX(time) FROM weather_hourly")).scalar()
                weather_sample = pd.read_sql(
                    text("SELECT * FROM weather_hourly ORDER BY time DESC LIMIT 5"),
                    conn
                )
                validation_info["tables"]["weather_hourly"] = {
                    "row_count": int(weather_count),
                    "latest_time": str(weather_latest),
                    "sample_data": weather_sample.to_dict('records') if len(weather_sample) > 0 else []
                }

            return validation_info, 200

        except Exception:
            # Log the full traceback server-side (visible in kubectl logs) but return a generic message to the client
            logger.exception("Validation endpoint DB error")  # full stack trace to pod stdout
            return {"status": "error", "message": "Internal server error"}, 500  # no DB details exposed

    @app.errorhandler(429)
    def ratelimit_handler(e):
        # Return JSON so Dash callbacks can parse the response instead of throwing SyntaxError
        return {"error": "Rate limit exceeded", "message": "Too many requests — please try again later."}, 429

    @app.errorhandler(404)
    def not_found(e):
        # Return a friendly HTML page with links — more useful than JSON for a browser visitor
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Page Not Found</title>
  <style>
    /* Colors match design_tokens.py — keep in sync manually */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #0f1117; color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex; align-items: center; justify-content: center; min-height: 100vh;
    }
    .card {
      background: #1a1d27; border: 1px solid #2d3348;
      border-top: 3px solid #3b82f6; border-radius: 12px;
      padding: 48px 40px; max-width: 480px; width: 90%; text-align: center;
    }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 12px; }
    p  { color: #8892a4; font-size: 14px; line-height: 1.7; margin-bottom: 28px; }
    .links { display: flex; flex-direction: column; gap: 12px; }
    a {
      display: block; padding: 12px 20px; border-radius: 8px;
      background: #3b82f6; color: #fff; text-decoration: none;
      font-size: 14px; font-weight: 600;
    }
    a:hover { background: #2563eb; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Page not found</h1>
    <p>The URL you visited doesn&rsquo;t exist. Here are the two dashboards:</p>
    <div class="links">
      <a href="/dashboard/">Stocks Dashboard</a>
      <a href="/weather/">Weather Dashboard</a>
    </div>
  </div>
</body>
</html>""", 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Unhandled server error")  # log full details server-side
        return {"error": "Internal server error"}, 500  # generic message — no stack trace to client
