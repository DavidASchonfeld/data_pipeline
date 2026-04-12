import hmac
import os
from functools import wraps

from flask import Flask, Response, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# ── Rate Limiter ──────────────────────────────────────────────────────────────
# Uses in-process memory storage — one counter per Gunicorn worker.
# Trade-off: with 2 workers a bot can send ~2× the stated limit before being blocked.
# Production upgrade path: set LIMITER_STORAGE_URI=redis://... in the K8s secret.
limiter = Limiter(
    key_func=get_remote_address,          # rate limit per client IP address
    default_limits=["100 per minute"],    # global fallback — also covers /_dash-update-component callbacks
    storage_uri="memory://",              # in-process memory; acceptable for low-traffic portfolio dashboard
)

# ── Content-Security-Policy ───────────────────────────────────────────────────
# Dash 2.x inlines React + Plotly as <script> blocks — nonces are not supported,
# so 'unsafe-inline' is required for script-src and style-src.
_CSP = {
    "default-src": "'self'",
    "script-src":  "'self' 'unsafe-inline'",   # Dash embeds React/Plotly as inline scripts
    "style-src":   "'self' 'unsafe-inline'",   # Plotly injects inline styles onto SVG elements
    "img-src":     "'self' data:",              # Plotly chart PNG export uses data: URIs
    "font-src":    "'self'",
    "connect-src": "'self'",                    # Dash callbacks POST to /_dash-update-component (same origin)
    "frame-ancestors": "'none'",               # prevents iframe embedding (clickjacking defense)
    "object-src":  "'none'",                   # no Flash / plugin objects
    "base-uri":    "'self'",                   # blocks injected <base> tag from hijacking relative URLs
}

# ── Basic Auth helper ─────────────────────────────────────────────────────────
def require_basic_auth(f):
    """Decorator: enforce HTTP Basic Auth using VALIDATION_USER/PASS env vars."""
    @wraps(f)  # preserves original function name so Flask's route registry stays correct
    def decorated(*args, **kwargs):
        auth = request.authorization                                     # Flask parses Authorization header
        expected_user = os.environ.get("VALIDATION_USER", "").encode()  # credential from K8s secret
        expected_pass = os.environ.get("VALIDATION_PASS", "").encode()  # credential from K8s secret
        # hmac.compare_digest prevents timing attacks when comparing credential strings
        user_ok = bool(auth) and hmac.compare_digest(auth.username.encode(), expected_user)
        pass_ok = bool(auth) and hmac.compare_digest(auth.password.encode(),  expected_pass)
        if not (user_ok and pass_ok):
            # WWW-Authenticate header triggers the browser's native username/password dialog
            return Response(
                "Unauthorized",
                401,
                {"WWW-Authenticate": 'Basic realm="Validation endpoint"'},
            )
        return f(*args, **kwargs)
    return decorated


def init_security(app: Flask) -> None:
    """Attach rate limiting, security headers, and CORS to the Flask app.

    Must be called after app = Flask(__name__) but before routes are registered,
    so the limiter is active on every route from the moment it is defined.
    """
    # Initialize rate limiter — hooks into Flask's before/after_request lifecycle
    limiter.init_app(app)

    # Exempt /health entirely so K8s liveness/readiness probes are never blocked
    # (probing every 5-10s from one source would quickly exhaust per-IP limits)
    app.view_functions  # ensure view registry exists before we reference it

    # Initialize security headers — attaches an after_request hook on every response
    Talisman(
        app,
        force_https=False,                             # no TLS at app layer; NodePort serves plain HTTP
        strict_transport_security=False,               # HSTS only works over HTTPS — skip until TLS ingress added
        frame_options="DENY",                          # X-Frame-Options: DENY (clickjacking defense)
        content_security_policy=_CSP,
        referrer_policy="strict-origin-when-cross-origin",  # limits referrer leakage on cross-origin requests
        permissions_policy={                           # disable browser APIs the dashboard never uses
            "geolocation": "()",
            "microphone": "()",
            "camera":      "()",
        },
    )

    # Initialize CORS — restricts cross-origin requests to the domain in ALLOWED_ORIGINS.
    # Empty string (the default) means no cross-origin access is granted.
    # Dash itself is same-origin and is unaffected — CORS headers only appear when
    # a request includes an Origin header from a different domain.
    CORS(
        app,
        resources={r"/*": {"origins": os.environ.get("ALLOWED_ORIGINS", "")}},
    )
