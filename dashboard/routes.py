import logging
import pandas as pd
from flask import Flask
from sqlalchemy import text

from db import DB_ENGINE
from security import limiter, require_basic_auth  # rate limiting decorators and basic-auth helper

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

    @app.errorhandler(404)
    def not_found(e):
        # Return clean JSON — no Flask version or internal route details exposed
        return {"error": "Not found"}, 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Unhandled server error")  # log full details server-side
        return {"error": "Internal server error"}, 500  # generic message — no stack trace to client
