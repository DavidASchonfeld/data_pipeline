"""
Spot instance interruption detection + UI notification components.

AWS Spot Instances can be reclaimed by AWS at any time with just a 2-minute notice.
This module polls the EC2 instance metadata service (IMDS) every 5 seconds.
When a notice is detected, a countdown banner is shown to all website visitors.
If the server is not a spot instance (or not on AWS at all), this module does nothing.
"""

import logging
import time
from datetime import datetime, timezone

import requests
from dash import dcc, html
from dash.dependencies import Input, Output

logger = logging.getLogger(__name__)  # module-level logger — writes to pod stdout

# ── IMDSv2 endpoints ──────────────────────────────────────────────────────────
# IMDSv2 requires a token first (PUT), then the actual metadata request (GET).
# AWS best practice — required on instances where IMDSv1 is disabled.
_IMDS_TOKEN_URL = "http://169.254.169.254/latest/api/token"
_IMDS_SPOT_URL  = "http://169.254.169.254/latest/meta-data/spot/termination-time"

# ── In-process result cache ───────────────────────────────────────────────────
# Shared across all Dash callback invocations within one Gunicorn worker.
# Prevents hammering IMDS on every 5-second poll from every connected browser tab.
# CPython's GIL makes dict assignment thread-safe — no lock needed here.
_spot_cache: dict = {}
_CACHE_TTL_S = 3   # seconds — re-poll IMDS at most once per 3 seconds per worker


def check_spot_interruption() -> dict:
    """Check EC2 IMDS for a spot termination notice using IMDSv2.

    Returns {"interruption": False, "termination_time": None} during normal operation.
    Returns {"interruption": True,  "termination_time": "<ISO 8601 string>"} when AWS
    has posted a shutdown notice.

    Always returns safely — never raises an exception — so any non-EC2 environment
    (on-demand instance, local dev, future cloud migration) is a silent no-op.
    Result is cached for _CACHE_TTL_S seconds to avoid unnecessary IMDS traffic.
    """
    now = time.monotonic()

    # Return cached result if it is still fresh
    if _spot_cache and (now - _spot_cache["ts"]) < _CACHE_TTL_S:
        return _spot_cache["result"]

    result = _do_imds_check()  # make the actual network request

    # Overwrite cache (dict assignment is atomic in CPython)
    _spot_cache["result"] = result
    _spot_cache["ts"] = now
    return result


def _do_imds_check() -> dict:
    """Make the two-step IMDSv2 network call; always returns a safe result dict."""
    safe_default = {"interruption": False, "termination_time": None}

    try:
        # Step 1: obtain a short-lived IMDSv2 session token (TTL 10 s is plenty)
        token_resp = requests.put(
            _IMDS_TOKEN_URL,
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "10"},
            timeout=1,  # 1 s hard cap — EHOSTUNREACH is instant on non-EC2; 1 s guards edge-case VPCs
        )
        token = token_resp.text

        # Step 2: request the termination timestamp, authenticated with the token
        spot_resp = requests.get(
            _IMDS_SPOT_URL,
            headers={"X-aws-ec2-metadata-token": token},
            timeout=1,
        )

        if spot_resp.status_code == 200:
            # 200 means AWS has posted a termination time — interruption is imminent
            term_time = spot_resp.text.strip()
            logger.warning("Spot interruption notice received — termination at %s", term_time)
            return {"interruption": True, "termination_time": term_time}

        # 404 is normal (no termination pending); any other code also treated as safe
        return safe_default

    except requests.exceptions.RequestException:
        # IMDS unreachable — running on-demand, locally, or on another cloud
        return safe_default
    except Exception:
        # Unexpected error (malformed response, etc.) — log but never crash the app
        logger.exception("Unexpected error polling EC2 IMDS spot termination endpoint")
        return safe_default


# ── Dash layout helpers ───────────────────────────────────────────────────────

def build_spot_layout_components(prefix: str) -> list:
    """Return the invisible Dash components + spot banner for insertion into a layout.

    prefix: short string ("stocks" or "weather") that namespaces every component ID
    so the two Dash apps sharing one Flask process have no ID conflicts.

    Usage in app.py:
        children=[*build_spot_layout_components("stocks"), html.H1(...), ...]
    """
    return [
        # Polls IMDS every 5 s; result feeds spot-store via the poll callback
        dcc.Interval(
            id=f"{prefix}-spot-poll",
            interval=5_000,  # 5 000 ms — comfortably within the 2-minute AWS notice window
            n_intervals=0,
        ),
        # Enabled only when interruption=True; ticks every 1 s to drive the countdown display
        dcc.Interval(
            id=f"{prefix}-spot-countdown",
            interval=1_000,  # 1 000 ms — smooth per-second countdown update
            n_intervals=0,
            disabled=True,   # starts disabled; enabled when a notice arrives
        ),
        # Client-side memory store: holds the latest result from check_spot_interruption()
        dcc.Store(id=f"{prefix}-spot-store", data=None),
        # ── Spot interruption banner ───────────────────────────────────────────
        # Fixed-position toast, bottom-right corner; hidden until interruption is detected
        html.Div(
            id=f"{prefix}-spot-banner",
            className="spot-banner",
            style={"display": "none"},  # hidden by default; callback switches to display:flex
            children=[
                # Warning icon on the left side of the banner
                html.Div(className="spot-banner__icon", children="\u26a0"),  # ⚠ unicode warning sign
                html.Div(
                    className="spot-banner__body",
                    children=[
                        html.P(
                            "Heads up \u2014 switching servers",  # em-dash for clean typography
                            className="spot-banner__title",
                        ),
                        html.P(
                            "The site will go offline briefly in:",
                            className="spot-banner__subtitle",
                        ),
                        # Large countdown — updated every second by the countdown callback
                        html.P(
                            id=f"{prefix}-spot-countdown-display",
                            className="spot-banner__countdown",
                            children="\u2014",  # em-dash placeholder until first tick
                        ),
                        html.P(
                            # Spot instances cost ~75% less; AWS occasionally reclaims them
                            "This dashboard uses discounted spare-capacity hosting "
                            "to keep costs ~75% lower. A new server is already "
                            "booting and the site will be back in about a minute.",
                            className="spot-banner__note",
                        ),
                    ],
                ),
            ],
        ),
    ]


# ── Dash callbacks ────────────────────────────────────────────────────────────

# ── Server-offline detection helpers ─────────────────────────────────────────
# These complement the spot-interruption banner above: spot.py warns when AWS is
# *about* to shut down the server; these warn when the server is *already* down.

def build_offline_layout_components(prefix: str) -> list:
    """Return the Dash components that power the server-offline banner.

    prefix: short string ("stocks" or "weather") that namespaces every ID
    so the two Dash apps sharing one Flask process have no ID conflicts.
    Works alongside build_spot_layout_components — add both to each layout.
    """
    return [
        # Browser-side timer: fires every 15 s so the JS health check stays current
        dcc.Interval(
            id=f"{prefix}-health-poll",
            interval=15_000,   # 15 000 ms — quick enough to detect outages without hammering
            n_intervals=0,
        ),
        # Offline warning banner — displayed at the top of the page when /health is unreachable
        html.Div(
            id=f"{prefix}-offline-banner",
            className="offline-banner",
            style={"display": "none"},  # hidden by default; clientside callback shows it on failure
            children=[
                html.Div(className="offline-banner__icon", children="\u26a0"),  # ⚠ warning sign
                html.Div(
                    className="offline-banner__body",
                    children=[
                        html.P(
                            "Dashboard temporarily offline",
                            className="offline-banner__title",
                        ),
                        html.P(
                            "The server is not responding — your data may be stale. "
                            "Please refresh this page in a few minutes.",
                            className="offline-banner__message",
                        ),
                    ],
                ),
            ],
        ),
    ]


def register_offline_callbacks(dash_app, prefix: str) -> None:
    """Register the clientside health-check callback for the offline banner.

    Uses a JavaScript fetch() call to ping /health from the browser — this runs
    even when the server is down, because the JS is already loaded in the page.
    When the fetch fails, the banner is shown; when it succeeds, it stays hidden.
    """
    # Clientside callback: runs entirely in the browser every 15 s — no server round-trip needed
    # Differentiates 503 (cache warming — normal at startup) from a true outage or network failure
    dash_app.clientside_callback(
        """
        function(n_intervals) {
            return fetch('/health/ready', {cache: 'no-store'})
                .then(function(r) {
                    // 200 — server is up and cache is warm; keep banner hidden
                    if (r.ok) return {'display': 'none'};
                    // 503 — server is still pre-warming its data cache; this is normal at startup
                    // and during spot replacement — recovery.js handles the reload, so hide the
                    // banner here to avoid a false "server offline" alarm for the user
                    if (r.status === 503) return {'display': 'none'};
                    // any other error code (502, 504, etc.) — unexpected problem; show banner
                    return {'display': 'flex'};
                })
                .catch(function() {
                    // fetch failed entirely — server is unreachable; show banner
                    return {'display': 'flex'};
                });
        }
        """,
        Output(f"{prefix}-offline-banner", "style"),
        Input(f"{prefix}-health-poll", "n_intervals"),
    )
# ─────────────────────────────────────────────────────────────────────────────


def register_spot_callbacks(dash_app, prefix: str) -> None:
    """Register the three callbacks that power the spot interruption banner.

    prefix must match the prefix passed to build_spot_layout_components() for the same app.
    Call this after the Dash app's layout has been set.

    Callback 1: 5-s interval → poll IMDS → write result to store.
    Callback 2: store state change → enable or disable the 1-s countdown interval.
    Callback 3: store + 1-s tick → update banner visibility and M:SS countdown text.
    """

    # ── Callback 1: poll IMDS on every 5-second tick ──────────────────────────
    @dash_app.callback(
        Output(f"{prefix}-spot-store", "data"),
        Input(f"{prefix}-spot-poll", "n_intervals"),
        prevent_initial_call=False,  # populate store immediately on page load (n_intervals=0)
    )
    def poll_spot_status(n_intervals):
        """Ask the server every 5 s whether AWS has issued a shutdown notice."""
        return check_spot_interruption()

    # ── Callback 2: enable/disable the 1-s countdown interval ────────────────
    @dash_app.callback(
        Output(f"{prefix}-spot-countdown", "disabled"),
        Input(f"{prefix}-spot-store", "data"),
    )
    def toggle_countdown_interval(store_data):
        """Start the per-second ticker only when a shutdown is pending — saves resources otherwise."""
        if not store_data:
            return True  # no data yet — keep ticker disabled
        # disabled=True when no interruption; disabled=False when interruption=True
        return not store_data.get("interruption", False)

    # ── Callback 3: update banner visibility and countdown text ───────────────
    @dash_app.callback(
        Output(f"{prefix}-spot-banner", "style"),
        Output(f"{prefix}-spot-countdown-display", "children"),
        Input(f"{prefix}-spot-store", "data"),
        Input(f"{prefix}-spot-countdown", "n_intervals"),
    )
    def update_banner(store_data, n_intervals):
        """Show/hide the banner and format the M:SS countdown from the termination timestamp."""
        hidden  = {"display": "none"}   # banner off
        visible = {"display": "flex"}   # flex aligns icon and body side by side

        if not store_data or not store_data.get("interruption"):
            return hidden, "\u2014"  # no interruption — keep banner hidden

        term_str = store_data.get("termination_time")
        if not term_str:
            # Interruption flagged but timestamp missing — show banner without countdown
            return visible, "\u2014"

        try:
            # Parse the ISO 8601 timestamp from AWS (e.g. "2024-11-14T12:34:56Z")
            term_dt = datetime.fromisoformat(term_str.replace("Z", "+00:00"))
            delta   = term_dt - datetime.now(timezone.utc)
            secs    = max(0, int(delta.total_seconds()))  # clamp to 0 — never show negative
            mins, remaining_secs = divmod(secs, 60)
            countdown_text = f"{mins}:{remaining_secs:02d}"  # e.g. "1:47" or "0:09"
        except Exception:
            # Malformed timestamp — show the banner but skip the countdown rather than crashing
            logger.exception("Failed to parse spot termination timestamp: %s", term_str)
            countdown_text = "\u2014"

        return visible, countdown_text
