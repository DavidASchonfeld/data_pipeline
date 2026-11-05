# The genai-api HTTP service: one tiny Flask app that turns the agent's single public function,
# genai.agent.answer(question) -> AgentResult, into a JSON endpoint the dashboard can POST to.
#
# Why a separate service at all: answer() transitively loads the LLM SDK, the sentence-transformers
# embedder (~620 MB peak) and the pgvector/Snowflake clients. The dashboard runs in a separate, slim,
# internet-facing image with a 512 Mi memory cap and none of those deps. Rather than fatten the public
# web pod, this service runs from the existing Airflow image (which already bakes every dep + the model
# weights + the creds) in the airflow-my-namespace, and the dashboard calls it over in-cluster DNS.
#
# The service is deliberately minimal: two routes, no auth (it is ClusterIP-only — the dashboard's
# flask-limiter is the public front door), and it never touches a provider's wire format itself — all
# of that lives behind answer().

from __future__ import annotations

import dataclasses
import logging
import time

from flask import Flask, jsonify, request

from genai.config import GENAI_MAX_QUESTION_CHARS, GENAI_SERVICE_PORT

logger = logging.getLogger(__name__)

app = Flask(__name__)


def _serialize(result) -> dict:
    # Turn the AgentResult dataclass (and its nested TraceStep / Passage dataclasses) into plain JSON.
    # dataclasses.asdict recurses through the whole tree, so trace steps and source passages — including
    # each passage's metadata dict — come out as ready-to-serialize nested dicts.
    return dataclasses.asdict(result)


@app.get("/health")
def health():
    # Liveness/readiness probe target — cheap and dependency-free so the pod is marked Ready as soon as
    # the web server is up, without waiting on the LLM/embedder/Snowflake being reachable.
    return jsonify({"status": "ok"})


@app.post("/agent/answer")
def agent_answer():
    # The one real route: take {"question": "..."}, run the agent, return the full AgentResult as JSON.
    # answer() is imported lazily (inside the handler) so the module imports cheaply in tests that only
    # need the Flask test client, and so an import-time failure in the heavy genai stack surfaces here
    # as a clean 500 on the first request rather than killing the whole process at startup.
    from genai.agent import AgentError, answer

    payload = request.get_json(silent=True) or {}
    question = payload.get("question")

    # Validate the input defensively before spending anything. A non-string, blank, or over-long
    # question is a client error (400), never a server crash — this is the unbounded-consumption guard
    # (OWASP LLM10). The per-question cost cap itself is enforced inside answer().
    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "Field 'question' must be a non-empty string."}), 400
    if len(question) > GENAI_MAX_QUESTION_CHARS:
        return jsonify({
            "error": f"Question is too long ({len(question)} chars); "
                     f"the limit is {GENAI_MAX_QUESTION_CHARS}.",
        }), 400

    started = time.monotonic()
    try:
        result = answer(question)
    except AgentError as exc:
        # The agent layer already wrapped any provider/tool failure as AgentError. Surface it as a 502
        # (the upstream LLM/data dependency failed) with a clean message — never leak a stack trace.
        logger.warning("agent answer failed: %s", exc)
        return jsonify({"error": f"The assistant could not answer: {exc}"}), 502

    body = _serialize(result)
    # Wall-clock time the question took, so the UI can show "answered in N.Ns" alongside the cost.
    body["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return jsonify(body)


def main() -> None:
    # Local/dev entry point only — in the pod the service runs under gunicorn (see the Deployment
    # manifest), which imports `app` directly and never calls this. Flask's built-in server is fine for
    # a quick local smoke test.
    app.run(host="0.0.0.0", port=GENAI_SERVICE_PORT)


if __name__ == "__main__":
    main()
