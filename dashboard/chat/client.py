"""HTTP client for the genai-api service.

The agent itself runs in a separate pod (it needs the heavy genai runtime + the embedding model that
won't fit in this slim dashboard image). This module is the one place the dashboard talks to it: a
single POST to the in-cluster service. Every failure mode — service down, slow, or a non-200 — is
funnelled into one well-named exception (ChatServiceError) so the callback layer has exactly one thing
to catch, per the project's "one exception type per layer" rule.
"""

import logging

import requests

from config import GENAI_API_URL

logger = logging.getLogger(__name__)

# Connect fast (5s — the service is one DNS hop away), but allow a long read: a multi-tool agent answer
# can take tens of seconds, and this matches the genai-api gunicorn 120s request timeout.
_TIMEOUT = (5, 120)


class ChatServiceError(Exception):
    """Raised for any failure talking to the genai-api service — unreachable, timed out, or a non-200
    response. Carries a human-readable message the chat UI can show as-is (never a stack trace)."""


def ask(question: str) -> dict:
    """POST the question to the genai-api service and return the parsed AgentResult JSON
    (answer_text, trace, sources, cost_estimate, elapsed_seconds). Raises ChatServiceError on any
    transport or HTTP error so the caller has a single failure path to handle."""
    url = f"{GENAI_API_URL}/agent/answer"
    try:
        resp = requests.post(url, json={"question": question}, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        # Connection refused, DNS failure, read timeout — the service is down or too slow.
        logger.warning("genai-api request failed: %s", exc)
        raise ChatServiceError("The assistant service is unavailable right now. Please try again shortly.") from exc

    if resp.status_code != 200:
        # The service answered but with an error (e.g. 400 too-long question, 502 upstream LLM failure).
        # Surface its message when it sent one, otherwise a generic note tied to the status code.
        detail = ""
        try:
            detail = resp.json().get("error", "")
        except ValueError:
            pass
        logger.warning("genai-api returned %s: %s", resp.status_code, detail)
        raise ChatServiceError(detail or f"The assistant returned an error (HTTP {resp.status_code}).")

    return resp.json()
