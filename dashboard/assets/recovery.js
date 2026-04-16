/**
 * recovery.js — crash recovery overlay for Dash.
 *
 * Runs outside React's tree so it survives Redux/React crashes.
 * When the dashboard crashes (e.g. server returns HTML instead of JSON
 * during a spot instance replacement), this script detects the crash,
 * shows a fullscreen "Reconnecting" overlay, polls /health/ready,
 * and auto-reloads the page once the server is healthy again.
 *
 * Three detection paths:
 *  1. Error events (SyntaxError, Redux crash) — caught by window error listeners;
 *     a 3-second health check decides whether to reload or show the overlay.
 *  2. console.error interceptor — catches "Callback failed" errors that Dash logs
 *     internally without throwing; triggers the same 3-second health check.
 *  3. Periodic health monitor — polls /health/ready every 10 s after first render;
 *     shows overlay if the server goes offline mid-session (e.g. spot replacement while
 *     the page is open and Dash is actively making requests).
 *
 * Loaded automatically by Dash from the assets/ directory.
 * Protects both the stocks (/dashboard/) and weather (/weather/) apps.
 */

"use strict";

(function () {
    // how often to poll /health/ready once the overlay is shown
    var POLL_MS = 5000;
    // how long to wait after an empty container before showing overlay (filters normal re-renders)
    var DEBOUNCE_MS = 500;
    // how long to wait after an error before health-checking the server
    var ERROR_WAIT_MS = 3000;
    // how often to poll the server for health while the page is open mid-session
    var HEALTH_MONITOR_MS = 10000;
    // grace period after page load where callback errors are expected (slow cache warm or warehouse resume)
    var PAGE_LOAD_GRACE_MS = 15000;
    // Dash renders its app inside this element
    var CONTAINER_ID = "_dash-app-content";

    var _pageLoadTime = Date.now(); // captured once at script load — used for PAGE_LOAD_GRACE_MS check

    var _hasRendered = false;   // true after Dash's container first gets children
    var _overlayActive = false; // prevents duplicate overlay creation
    var _debounceTimer = null;  // debounce timer for empty-container detection
    var _errorTimer = null;     // waits ERROR_WAIT_MS then health-checks server
    var _healthMonitor = null;  // setInterval ID for periodic mid-session health polling

    // ── Overlay styles — uses CSS variables from theme.css for consistency ───
    function _injectStyles() {
        var style = document.createElement("style");
        style.textContent =
            // fullscreen dark backdrop covering the broken Dash page
            "#dash-recovery-overlay {" +
            "  position: fixed; top: 0; left: 0; width: 100%; height: 100%;" +
            "  z-index: 99999;" +
            "  display: flex; align-items: center; justify-content: center;" +
            "  background-color: var(--bg-base, #0f1117);" +
            "}" +
            // centered card matching the S3 loading page layout
            ".recovery-card {" +
            "  background: var(--bg-surface, #1a1d27);" +
            "  border: 1px solid var(--border, #2d3348);" +
            "  border-top: 3px solid var(--accent-blue, #3b82f6);" +
            "  border-radius: 12px;" +
            "  padding: 48px 40px; max-width: 520px; width: 90%; text-align: center;" +
            "}" +
            // spinner matching the S3 loading page animation
            ".recovery-spinner {" +
            "  width: 48px; height: 48px;" +
            "  border: 4px solid var(--border, #2d3348);" +
            "  border-top-color: var(--accent-blue, #3b82f6);" +
            "  border-radius: 50%;" +
            "  animation: recovery-spin 1s linear infinite;" +
            "  margin: 0 auto 28px;" +
            "}" +
            "@keyframes recovery-spin { to { transform: rotate(360deg); } }" +
            ".recovery-card h1 {" +
            "  font-size: 22px; font-weight: 700;" +
            "  color: var(--text-primary, #e2e8f0);" +
            "  margin-bottom: 16px;" +
            "}" +
            ".recovery-card .recovery-explanation {" +
            "  color: var(--text-secondary, #8892a4);" +
            "  font-size: 14px; line-height: 1.7; margin-bottom: 24px;" +
            "}" +
            ".recovery-card .recovery-status {" +
            "  color: var(--accent-blue, #3b82f6);" +
            "  font-size: 13px; font-weight: 600;" +
            "}" +
            ".recovery-card .recovery-muted {" +
            "  color: var(--text-muted, #4a5568);" +
            "  font-size: 12px; margin-top: 20px; line-height: 1.5;" +
            "}";
        document.head.appendChild(style);
    }

    // ── Show the recovery overlay and start polling ─────────────────────────
    function _showOverlay() {
        if (_overlayActive) return; // only one overlay at a time
        _overlayActive = true;

        // stop the health monitor — overlay's own poll takes over
        if (_healthMonitor) { clearInterval(_healthMonitor); _healthMonitor = null; }

        _injectStyles();

        var overlay = document.createElement("div");
        overlay.id = "dash-recovery-overlay";
        overlay.innerHTML =
            '<div class="recovery-card">' +
            '  <div class="recovery-spinner"></div>' +
            '  <h1>Reconnecting&hellip;</h1>' +
            '  <p class="recovery-explanation">' +
            '    The dashboard lost its connection to the server. ' +
            '    This usually happens when the server is switching to a new host.' +
            '  </p>' +
            '  <p class="recovery-status" id="recovery-status-text">' +
            '    Checking server status&hellip;' +
            '  </p>' +
            '  <p class="recovery-muted">' +
            '    This page will reload automatically when the server is ready.' +
            '  </p>' +
            '</div>';

        document.body.appendChild(overlay);
        _startHealthPoll();
    }

    // ── Poll /health/ready until 200, then reload ───────────────────────────
    function _startHealthPoll() {
        var statusEl = document.getElementById("recovery-status-text");

        var pollId = setInterval(function () {
            fetch("/health/ready", { cache: "no-store" })
                .then(function (r) {
                    if (r.ok) {
                        // server is healthy and cache is warm — safe to reload
                        clearInterval(pollId);
                        if (statusEl) statusEl.textContent = "Server is ready \u2014 reloading\u2026";
                        window.location.reload();
                    } else {
                        // server alive but still warming up (503 from /health/ready)
                        if (statusEl) statusEl.textContent = "Server is starting up\u2026";
                    }
                })
                .catch(function () {
                    // server unreachable — keep polling
                    if (statusEl) statusEl.textContent = "Server unreachable \u2014 retrying\u2026";
                });
        }, POLL_MS);
    }

    // ── Mid-session health monitor — catches server loss while page is open ──
    // Started once Dash first renders; polls /health/ready every 10 s.
    // Covers the case where the server goes down mid-session and Dash's callbacks
    // fail ("network connection was lost") without clearing the page container.
    function _startHealthMonitor() {
        if (_healthMonitor) return; // only one monitor at a time
        _healthMonitor = setInterval(function () {
            if (_overlayActive) return;
            fetch("/health/ready", { cache: "no-store" })
                .then(function (r) {
                    // 503 is normal during startup — offline banner handles it, not the overlay
                    // any other non-ok status (502, 504, etc.) is unexpected — show overlay
                    if (!r.ok && r.status !== 503) { _showOverlay(); }
                })
                .catch(function () {
                    // fetch failed entirely — server has disappeared (spot replacement)
                    _showOverlay();
                });
        }, HEALTH_MONITOR_MS);
    }

    // ── Detection: container became empty after Dash had rendered ────────────
    function _onContainerMutation() {
        var container = document.getElementById(CONTAINER_ID);
        if (!container) return;

        if (!_hasRendered && container.childNodes.length > 0) {
            // Dash has rendered its first children — begin mid-session monitoring
            _hasRendered = true;
            _startHealthMonitor();
            // intentionally NOT cancelling _errorTimer here: container children only
            // mean Dash rendered its loading skeleton, not that the layout fetched OK
            return;
        }

        if (_hasRendered && container.childNodes.length === 0) {
            // container emptied — debounce to filter out normal Dash re-renders
            if (!_debounceTimer) {
                _debounceTimer = setTimeout(function () {
                    _debounceTimer = null;
                    // re-check after debounce — still empty means genuine crash
                    var el = document.getElementById(CONTAINER_ID);
                    if (el && el.childNodes.length === 0) {
                        _showOverlay();
                    }
                }, DEBOUNCE_MS);
            }
        } else if (_debounceTimer) {
            // container re-populated during debounce — false alarm, cancel
            clearTimeout(_debounceTimer);
            _debounceTimer = null;
        }
    }

    // ── Detection: global error listener for Redux/Dash crashes ─────────────
    function _onGlobalError(event) {
        if (_overlayActive) return;
        var msg = (event.message || event.reason || "").toString();

        // catch Dash/Redux-specific crashes — these leave the page blank
        var isReduxCrash = msg.indexOf("Minified Redux error") !== -1 || msg.indexOf("redux") !== -1;

        // catch JSON parse failure — server returns HTML instead of JSON during spot replacement;
        // check both instanceof and .name for cross-browser/realm reliability (Safari vs Chrome)
        var isJsonParseFail = event.type === "unhandledrejection" && (
            event.reason instanceof SyntaxError ||
            (event.reason && event.reason.name === "SyntaxError")
        );

        // catch "Callback failed: the server did not respond" — Dash's mid-session server-loss error
        var isCallbackFail = msg.indexOf("server did not respond") !== -1 ||
                             msg.indexOf("Callback failed") !== -1;

        if (!isReduxCrash && !isJsonParseFail && !isCallbackFail) return; // unrelated error — ignore

        // Within the first PAGE_LOAD_GRACE_MS after navigation, callback errors are expected:
        // Snowflake's warehouse may be resuming after auto-suspend, or the cache is cold.
        // Dash already shows an error message inside the component — no recovery action needed.
        if (isCallbackFail && (Date.now() - _pageLoadTime) < PAGE_LOAD_GRACE_MS) { return; }

        if (_errorTimer) return; // already handling an error — don't stack timers

        // Wait ERROR_WAIT_MS before acting: Dash may retry the request and recover on its own.
        // After the delay, health-check the server to decide the right response.
        _errorTimer = setTimeout(function () {
            _errorTimer = null;
            if (_overlayActive) return;
            fetch("/health/ready", { cache: "no-store" })
                .then(function (r) {
                    if (r.ok) {
                        // Callback-only failures mean the server is healthy and Dash already showed
                        // an error in the component — reloading would re-trigger the same slow
                        // queries and risk a reload loop. Let the user click Refresh instead.
                        if (isCallbackFail) { return; }
                        // True crashes (Redux error, JSON parse failure) + healthy server → reload
                        window.location.reload();
                    } else {
                        // server still starting up (503) or has a problem — show overlay
                        _showOverlay();
                    }
                })
                .catch(function () {
                    // server unreachable — spot replacement in progress; show overlay
                    _showOverlay();
                });
        }, ERROR_WAIT_MS);
    }

    // ── Detection: console.error interceptor for Dash's internally caught errors ─
    // Dash catches callback failures internally and logs via console.error — they never
    // bubble as window errors, so the listener above cannot catch them. Monkey-patching
    // console.error lets us intercept the message and call _onGlobalError immediately
    // instead of waiting up to 10 s for the periodic health monitor to trigger.
    var _origConsoleError = console.error.bind(console);
    console.error = function () {
        _origConsoleError.apply(console, arguments); // keep original logging intact
        var msg = Array.prototype.slice.call(arguments).join(" ");
        // Reuse the existing 3-second delay + health-check flow in _onGlobalError
        if (msg.indexOf("Callback failed") !== -1 || msg.indexOf("server did not respond") !== -1) {
            _onGlobalError({ message: msg });
        }
    };

    // ── Bootstrap: wait for the Dash container, then observe it ─────────────
    function _waitForContainer() {
        var container = document.getElementById(CONTAINER_ID);
        if (container) {
            // if Dash already rendered before this script ran, mark it and start monitor
            if (container.childNodes.length > 0) {
                _hasRendered = true;
                _startHealthMonitor();
            }
            // watch for children being added or removed
            var observer = new MutationObserver(_onContainerMutation);
            observer.observe(container, { childList: true });
            return;
        }
        // container not in DOM yet — Dash creates it dynamically
        requestAnimationFrame(_waitForContainer);
    }

    // ── Wire up detection and start ─────────────────────────────────────────
    window.addEventListener("error", _onGlobalError);
    window.addEventListener("unhandledrejection", _onGlobalError);
    _waitForContainer();
})();
