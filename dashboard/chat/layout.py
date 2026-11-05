"""Layout for the Chat tab — the static page tree.

Three stacked regions inside the standard `dash-page` shell: an input row, a live trace row, and an
output row with two side-by-side cards ("From your pipeline" on the left, "Claude's explanation" on the
right), with a cost/elapsed footer. The callback in callbacks.py fills the empty `id`-bearing Divs.
Mirrors the existing stocks/weather pages so it inherits the shared theme.css styling.
"""

from dash import dcc, html

# Verbatim explainer required by EPIC 10 — sets honest expectations about what the assistant can see.
_EXPLAINER = (
    "How this works: your question is searched against SEC filings and live data in this pipeline. "
    "Relevant pieces are passed to Claude, which writes a plain-English explanation. Claude does not "
    "see data your retrieval system did not find."
)


def build_layout() -> html.Div:
    """Return the Chat page layout. Called once by register() when the Dash app is created."""
    return html.Div(
        className="dash-page",
        children=[
            html.H1("Chat — Ask the Pipeline"),
            html.P(
                "Ask a plain-English question about the companies and weather in this pipeline.",
                className="dash-subtitle",
            ),

            # Cross-links back to the other two dashboards — same nav pattern as the stocks/weather pages.
            html.Div(className="dash-nav", children=[
                html.A("← Stocks Dashboard", href="/dashboard/", className="dash-nav__link"),
                html.A("Weather Dashboard →", href="/weather/", className="dash-nav__link"),
            ]),

            # Explainer card: what the assistant does and, importantly, what it cannot see.
            html.Div(className="chat-explainer", children=html.P(_EXPLAINER)),

            # ── Input row ────────────────────────────────────────────────────
            html.Label("Your question:"),
            dcc.Textarea(
                id="chat-question",
                placeholder="e.g. What were Apple's biggest risks?",
                className="chat-input",
                # maxLength mirrors the service's GENAI_MAX_QUESTION_CHARS guard so over-long input is
                # stopped in the browser before a round-trip; the service still re-checks server-side.
                maxLength=2000,
            ),
            html.Button("Ask", id="chat-ask-btn", n_clicks=0, className="dash-btn"),

            # dcc.Loading shows a spinner over all three output regions while the (blocking) agent call
            # runs — the page never looks frozen even though an answer can take tens of seconds.
            dcc.Loading(
                id="chat-loading",
                type="circle",
                children=[
                    # ── Live trace row ───────────────────────────────────────
                    html.Div(className="chat-trace-wrap", children=[
                        html.H2("What the assistant did"),
                        html.Div(id="chat-trace", className="chat-trace"),
                    ]),

                    # ── Two-window output row ────────────────────────────────
                    html.Div(className="chat-windows", children=[
                        html.Div(className="chat-window", children=[
                            html.H2("From your pipeline"),
                            html.Div(id="chat-sources", className="chat-sources"),
                        ]),
                        html.Div(className="chat-window", children=[
                            html.H2("Claude's explanation"),
                            html.Div(id="chat-answer", className="chat-answer"),
                        ]),
                    ]),

                    # Cost + elapsed footer, filled after each answer.
                    html.P(id="chat-meta", className="dash-freshness"),
                ],
            ),
        ],
    )
