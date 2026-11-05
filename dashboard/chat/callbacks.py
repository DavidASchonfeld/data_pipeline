"""Callbacks for the Chat tab — one callback, plus the render helpers it uses.

The render helpers are module-level (not closures) so the offline tests can call them directly with a
canned AgentResult dict and assert the produced component tree, without spinning up Dash.

Security note (OWASP LLM05 — improper output handling): both the model's answer and the retrieved
passages are untrusted text. They are rendered as Markdown / plain string children only — never with
`dangerously_allow_html` — so Dash/React escape them. Combined with the dashboard's existing
Content-Security-Policy (dashboard/security.py), that is the reuse the roadmap intends; no hand-rolled
XSS escaping is added here.
"""

from dash import Input, Output, State, dcc, html

from chat.client import ChatServiceError, ask


def render_trace(trace: list) -> html.Ul:
    """Render the agent's step-by-step trace as a list — one item per tool turn. A step that errored
    (e.g. a refused SQL query) shows its message in red so a graceful refusal is visible, not hidden."""
    items = []
    for step in trace:
        label = f"Turn {step.get('turn', '?')}: {step.get('tool', 'unknown')}"
        summary = step.get("result_summary") or ""
        children = [html.Strong(label)]
        if summary:
            children.append(html.Span(f" — {summary}"))
        if step.get("error"):
            children.append(html.Span(f" — {step['error']}", className="chat-trace__error"))
        items.append(html.Li(children, className="chat-trace__step"))
    if not items:
        return html.Ul([html.Li("No steps recorded.")], className="chat-trace__list")
    return html.Ul(items, className="chat-trace__list")


def render_sources(sources: list, trace: list) -> list:
    """Render the left window. Filing passages come from `sources` (vector-retrieved). Weather is a
    Snowflake text lookup, so it never appears in `sources` — it shows up in the trace as a
    get_weather_summary step; surface those here too so weather answers are attributed, not unsourced."""
    cards = []

    # Vector-retrieved filing passages: each is a Passage dict with text + metadata.
    for p in sources:
        meta = p.get("metadata") or {}
        badge = meta.get("ticker") or meta.get("city") or p.get("source") or "source"
        cards.append(html.Div(className="chat-source-card", children=[
            html.Span(str(badge), className="chat-source-badge"),
            html.P(p.get("text", ""), className="chat-source-text"),
        ]))

    # Weather sources live in the trace (a Snowflake lookup, not a vector hit) — show each as a card.
    for step in trace:
        if step.get("tool") == "get_weather_summary":
            city = (step.get("tool_input") or {}).get("city", "weather")
            cards.append(html.Div(className="chat-source-card", children=[
                html.Span(f"weather: {city}", className="chat-source-badge"),
                html.P(step.get("result_summary", ""), className="chat-source-text"),
            ]))

    if not cards:
        return [html.P("No matching sources were found in the pipeline.", className="dash-subtitle")]
    return cards


def render_answer(answer_text: str):
    """Render the right window: the model's prose. dcc.Markdown escapes HTML by default (no
    dangerously_allow_html), so untrusted model output is rendered safely as text/markdown."""
    return dcc.Markdown(answer_text or "", className="chat-answer-md")


def render_meta(result: dict) -> str:
    """The cost + elapsed footer shown beneath each answer."""
    cost = result.get("cost_estimate", 0.0) or 0.0
    elapsed = result.get("elapsed_seconds")
    parts = [f"Estimated cost ${cost:.4f}"]
    if elapsed is not None:
        parts.append(f"answered in {elapsed}s")
    return " · ".join(parts)


def register_callbacks(chat_app) -> None:
    """Wire the single Ask callback onto the chat Dash app."""

    @chat_app.callback(
        Output("chat-trace", "children"),
        Output("chat-sources", "children"),
        Output("chat-answer", "children"),
        Output("chat-meta", "children"),
        Input("chat-ask-btn", "n_clicks"),
        State("chat-question", "value"),
        prevent_initial_call=True,
    )
    def on_ask(n_clicks, question):
        question = (question or "").strip()
        # Empty input never reaches the service — just nudge the user, leaving the windows blank.
        if not question:
            return [], [], dcc.Markdown("_Please type a question first._"), ""

        try:
            result = ask(question)
        except ChatServiceError as exc:
            # The one failure path: show the friendly message in the answer window, blank the rest.
            return [], [], html.P(str(exc), className="chat-error"), ""

        return (
            render_trace(result.get("trace", [])),
            render_sources(result.get("sources", []), result.get("trace", [])),
            render_answer(result.get("answer_text", "")),
            render_meta(result),
        )
