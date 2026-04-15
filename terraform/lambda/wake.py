"""Wake Lambda — boots the pipeline when someone visits the dashboard URL.

Triggered by API Gateway. Checks whether the EC2 instance is running:
  - If sleeping: scales the ASG from 0 to 1 and shows a loading page.
  - If booting: shows a loading page while services start up.
  - If ready: redirects the visitor to the live dashboard.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error

import boto3


# AWS clients — created once per Lambda cold start to reuse connections
asg_client = boto3.client("autoscaling")
ssm_client = boto3.client("ssm")

# Environment variables set by Terraform
ASG_NAME = os.environ["ASG_NAME"]
DASHBOARD_EIP = os.environ["DASHBOARD_EIP"]
SSM_PARAM = os.environ["SSM_PARAM"]

# How long to wait when checking if the dashboard is healthy
HEALTH_CHECK_TIMEOUT = 5

# Loading page HTML — polished dark theme matching the dashboard, with SVG countdown ring
LOADING_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Pipeline Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1117;
            color: #e2e8f0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            animation: fadeIn 0.4s ease-in;
        }}
        /* Soft fade-in on each 10s meta refresh to avoid harsh flash */
        @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
        .card {{
            background: #1a1d27;
            border: 1px solid #2d3348;
            border-top: 3px solid #3b82f6;
            border-radius: 12px;
            text-align: center;
            max-width: 480px;
            width: 90%;
            padding: 2.5rem 2rem 2rem;
        }}
        h1 {{
            font-size: 1.6rem;
            color: #e2e8f0;
            margin-bottom: 0.4rem;
        }}
        .subtitle {{
            color: #8892a4;
            font-size: 0.9rem;
            margin-bottom: 2rem;
        }}
        /* Ring container — keeps the SVG centered */
        .ring-wrap {{
            display: flex;
            justify-content: center;
            margin-bottom: 1.75rem;
        }}
        .status {{
            font-size: 1rem;
            color: #e2e8f0;
            margin-bottom: 0.5rem;
        }}
        .detail {{
            font-size: 0.82rem;
            color: #8892a4;
            margin-bottom: 0;
        }}
        /* Cost-savings note at the bottom of the card */
        .why-note {{
            font-size: 12px;
            color: #8892a4;
            max-width: 360px;
            margin: 24px auto 0;
            line-height: 1.6;
            text-align: center;
        }}
        .why-note strong {{
            color: #e2e8f0;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Data Pipeline Dashboard</h1>
        <p class="subtitle">Real-time financial analytics and weather data</p>

        <!-- SVG circular progress ring with live countdown number inside -->
        <div class="ring-wrap">
            <svg viewBox="0 0 220 220" width="220" height="220" aria-hidden="true">
                <!-- Background track ring -->
                <circle cx="110" cy="110" r="90"
                        stroke="rgba(59,130,246,0.15)" stroke-width="8" fill="none"/>
                <!-- Animated progress arc — JS drives stroke-dashoffset -->
                <circle id="ring-arc" cx="110" cy="110" r="90"
                        stroke="#3b82f6" stroke-width="8" fill="none"
                        stroke-linecap="round"
                        stroke-dasharray="565.49"
                        stroke-dashoffset="565.49"
                        transform="rotate(-90 110 110)"/>
                <!-- Countdown text centered in the ring -->
                <text x="110" y="118"
                      text-anchor="middle" dominant-baseline="middle"
                      id="countdown-text"
                      font-size="52" font-weight="700"
                      fill="#e2e8f0" font-family="system-ui, sans-serif">{initial_display}</text>
            </svg>
        </div>

        <p class="status">{status_message}</p>
        <p class="detail">This page refreshes automatically every 10 seconds.</p>

        <p class="why-note">
            Why the wait? This dashboard automatically sleeps when it is not in use,
            saving approximately <strong>$10 per month</strong> compared to running
            a spare-capacity server around the clock, or up to
            <strong>$60/month</strong> compared to a standard on-demand server.
            The brief loading time is the trade-off for those savings.
        </p>
    </div>

    <script>
    (function () {{
        var CIRC = 565.49;  // ring circumference (2 * π * 90)

        // Store the very first arrival time in sessionStorage so the countdown
        // survives the 10-second meta refresh without resetting
        if (!sessionStorage.getItem('wakeStart')) {{
            sessionStorage.setItem('wakeStart', Date.now().toString());
            sessionStorage.setItem('wakeEstimate', '{estimated_seconds}');
        }}

        var startTime  = parseInt(sessionStorage.getItem('wakeStart'), 10);
        var totalSecs  = parseInt(sessionStorage.getItem('wakeEstimate'), 10);
        var arc        = document.getElementById('ring-arc');
        var display    = document.getElementById('countdown-text');

        function update() {{
            var elapsed   = (Date.now() - startTime) / 1000;
            var remaining = Math.max(0, totalSecs - elapsed);
            var progress  = elapsed / totalSecs;

            // Update progress ring — offset shrinks from full circumference to 0
            arc.style.strokeDashoffset = (CIRC * Math.max(0, 1 - progress)).toFixed(1);

            if (remaining <= 0) {{
                display.textContent = 'Almost ready\u2026';
                display.style.fontSize = '22px';
            }} else {{
                // Format as M:SS for human-readable countdown
                var m = Math.floor(remaining / 60);
                var s = Math.floor(remaining % 60);
                display.textContent = m + ':' + (s < 10 ? '0' : '') + s;
            }}
        }}

        update();                         // run immediately so there is no 1-second blank delay
        setInterval(update, 1000);        // tick every second
    }}());

    // Poll the Lambda every 3s via fetch(?check=1) — much faster than 10s meta refresh
    (function () {{
        function poll() {{
            var base = window.location.href.split('?')[0];
            fetch(base + '?check=1')
                .then(function (r) {{ return r.json(); }})
                .then(function (d) {{
                    if (d.ready) {{
                        // Navigate back to the bare API GW URL so Lambda returns the bridge page
                        window.location.replace(base);
                    }} else {{
                        setTimeout(poll, 3000);  // not ready yet — try again in 3s
                    }}
                }})
                .catch(function () {{ setTimeout(poll, 5000); }});  // network hiccup — retry in 5s
        }}
        setTimeout(poll, 3000);  // first check after 3s
    }}());
    </script>
</body>
</html>"""


def _update_last_activity():
    """Record the current time so the sleep Lambda knows someone visited recently."""
    ssm_client.put_parameter(
        Name=SSM_PARAM,
        Value=str(int(time.time())),
        Type="String",
        Overwrite=True,
    )


def _loading_response(status_message, estimated_seconds):
    """Return an HTML loading page that auto-refreshes until the dashboard is ready."""
    # Format estimated seconds as M:SS for the SVG text's initial value before JS runs
    mins = estimated_seconds // 60
    secs = estimated_seconds % 60
    initial_display = f"{mins}:{secs:02d}"
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": LOADING_PAGE_HTML.format(
            status_message=status_message,
            estimated_seconds=estimated_seconds,
            initial_display=initial_display,
        ),
    }


def _redirect_response(url):
    """Bridge page shown briefly while the user is still on the API Gateway URL.

    A bare 302 redirect to the EIP would move the browser's address bar to the
    raw EC2 IP. If the user bookmarks that IP and the server is sleeping on their
    next visit, they get a "connection dropped" error because no process is
    listening on that port. This page shows while the user is still on the always-
    available API Gateway URL, tells them to bookmark it, then forwards them to
    the live EIP via window.location.replace() after a short delay.
    """
    # JavaScript reads window.location.href at render time — that IS the API GW URL,
    # so we never need to hard-code or inject it server-side.
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Ready</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1117;
            color: #e2e8f0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            animation: fadeIn 0.4s ease-in;
        }}
        /* Matches the loading page fade-in so the transition feels continuous */
        @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
        .card {{
            background: #1a1d27;
            border: 1px solid #2d3348;
            border-top: 3px solid #22c55e;  /* green accent — server is ready */
            border-radius: 12px;
            text-align: center;
            max-width: 520px;
            width: 90%;
            padding: 2.5rem 2rem 2rem;
        }}
        h1 {{ font-size: 1.6rem; color: #e2e8f0; margin-bottom: 0.4rem; }}
        .subtitle {{ color: #8892a4; font-size: 0.9rem; margin-bottom: 1.75rem; }}
        /* Green tip box — visually distinct from the blue loading card */
        .tip-box {{
            background: #0c2118;
            border: 1px solid #166534;
            border-radius: 8px;
            padding: 1rem 1.2rem;
            margin-bottom: 1.5rem;
            text-align: left;
        }}
        .tip-label {{
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #4ade80;
            margin-bottom: 0.5rem;
        }}
        .tip-body {{
            font-size: 0.84rem;
            color: #86efac;
            line-height: 1.55;
            margin-bottom: 0.75rem;
        }}
        /* Monospace URL display so it reads as a link, not prose */
        .url-display {{
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
            font-size: 0.78rem;
            background: #071510;
            border: 1px solid #166534;
            border-radius: 4px;
            padding: 0.45rem 0.7rem;
            color: #4ade80;
            word-break: break-all;
            margin-bottom: 0.65rem;
        }}
        .btn {{
            display: inline-block;
            padding: 0.4rem 1.1rem;
            background: #166534;
            color: #bbf7d0;
            border: 1px solid #22c55e;
            border-radius: 6px;
            font-size: 0.82rem;
            text-decoration: none;
            cursor: pointer;
        }}
        .btn:hover {{ background: #14532d; }}
        .redirect-note {{ color: #8892a4; font-size: 0.82rem; margin-top: 0; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Dashboard is ready!</h1>
        <p class="subtitle">You will be forwarded automatically in a moment.</p>

        <!-- Tip box: shown while still on the API Gateway URL (the correct bookmark) -->
        <div class="tip-box">
            <p class="tip-label">Save this link</p>
            <p class="tip-body">
                Bookmark the URL currently in your address bar. It automatically wakes the
                server when it is sleeping, so future visits show a loading screen instead
                of a connection error.
            </p>
            <!-- JS fills these in from window.location.href at render time -->
            <div class="url-display" id="canonical-url">Loading&hellip;</div>
            <a class="btn" id="bookmark-link" href="#">Open &amp; Bookmark This Link</a>
        </div>

        <p class="redirect-note">Taking you to the live dashboard now&hellip;</p>
    </div>

    <script>
    (function () {{
        // window.location.href here is the API Gateway URL — the correct bookmark target.
        // We never hard-code it server-side; JS reads it fresh on every page load.
        var canonical = window.location.href;
        document.getElementById('canonical-url').textContent = canonical;
        document.getElementById('bookmark-link').href = canonical;

        // Use replace() so this bridge page does not add an extra entry to browser history;
        // the API Gateway URL stays as the previous history entry the user can return to.
        setTimeout(function () {{
            window.location.replace('{url}');
        }}, 2500);
    }}());
    </script>
</body>
</html>""",
    }


def _check_dashboard_health():
    """Ping /health/ready — returns 200 only after prewarm_cache() finishes all Snowflake queries."""
    try:
        url = f"http://{DASHBOARD_EIP}:32147/health/ready"
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT)
        return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _json_response(body: dict) -> dict:
    """Return a JSON API response — used for ?check=1 polling requests from the loading page."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    """Main entry point — API Gateway sends every visitor request here."""

    # Remember where the visitor was trying to go so we can send them there once the server is ready
    original_path = event.get("rawPath", "/")
    original_qs   = event.get("rawQueryString", "")  # preserve ?query=params too
    # Bare root requests default to the main dashboard page
    if not original_path or original_path == "/":
        original_path = "/dashboard/"

    # ?check=1 is sent by the loading page JS every 3s — return JSON instead of HTML
    query_params = event.get("queryStringParameters") or {}
    is_check = query_params.get("check") == "1"
    # Strip internal check param so it is never forwarded to the dashboard
    clean_qs = urllib.parse.urlencode({k: v for k, v in query_params.items() if k != "check"})

    # Every visit counts as activity so the sleep timer resets
    _update_last_activity()

    # Check the Auto Scaling Group to see if the instance is running
    asg_resp = asg_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[ASG_NAME]
    )
    asg = asg_resp["AutoScalingGroups"][0]
    desired = asg["DesiredCapacity"]
    instances = asg.get("Instances", [])

    # Instance is sleeping — wake it up by scaling the ASG from 0 to 1
    if desired == 0:
        print("Instance is sleeping — scaling ASG to 1")
        asg_client.set_desired_capacity(
            AutoScalingGroupName=ASG_NAME,
            DesiredCapacity=1,
        )
        if is_check:
            return _json_response({"ready": False})
        return _loading_response("Starting up the server...", 240)

    # Instance is booting — ASG has been told to launch but no instance is InService yet
    in_service = [i for i in instances if i["LifecycleState"] == "InService"]
    if not in_service:
        print("Instance is booting — waiting for InService")
        if is_check:
            return _json_response({"ready": False})
        return _loading_response("Server is booting up...", 180)

    # Instance is running — check if the dashboard is actually responding
    if _check_dashboard_health():
        # Redirect to the exact page the visitor originally requested, not always /dashboard/
        target = f"http://{DASHBOARD_EIP}:32147{original_path}"
        if clean_qs:
            target += f"?{clean_qs}"  # re-attach query params (check=1 already stripped)
        print(f"Dashboard is healthy — redirecting to {target}")
        if is_check:
            return _json_response({"ready": True, "url": target})
        return _redirect_response(target)

    # Instance is running but dashboard is not ready yet (K3s pods starting or prewarm in progress)
    print("Instance is InService but dashboard health check failed — still warming up")
    if is_check:
        return _json_response({"ready": False})
    return _loading_response("Dashboard is warming up...", 90)
