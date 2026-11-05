"""Chat tab — a drop-in Dash app that adds the RAG chat UI at /chat/.

This whole package is the plug-and-play unit the roadmap describes: removing the chat layer is deleting
this folder and the one `if config.GENAI_ENABLED` block in dashboard/app.py. It does NOT import the
genai package — the agent runs in a separate pod (genai-api), and dashboard/chat/client.py reaches it
over HTTP. So this folder carries none of the heavy genai dependencies.

Usage (from dashboard/app.py):
    if GENAI_ENABLED:
        from chat import register
        register(app)
"""

import os

import dash

from chat.callbacks import register_callbacks
from chat.layout import build_layout

# Serve the chat page from the SAME assets folder as the other dashboards so it inherits the shared
# theme.css (base palette, dash-page/dash-nav/dash-btn classes, plus the appended Chat-tab styles).
# Without pointing here, a third Dash app would look for assets under dashboard/chat/ and miss the theme.
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def register(server, url_base: str = "/chat/"):
    """Mount the Chat Dash app onto an existing Flask server at `url_base` and return it.

    Mirrors how weather_dash_app is mounted in dashboard/app.py: a new dash.Dash sharing the same Flask
    instance, with its own URL prefix and callback namespace so it never collides with the other apps.
    """
    chat_app = dash.Dash(
        __name__,
        server=server,
        url_base_pathname=url_base,
        assets_folder=_ASSETS,
    )
    chat_app.layout = build_layout()
    register_callbacks(chat_app)
    return chat_app
