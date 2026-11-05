# service: a thin HTTP wrapper around the EPIC 9 agent (genai.agent.answer). It exists so the slim,
# public-facing dashboard pod can ask questions over in-cluster HTTP instead of importing the whole
# genai runtime (LLM SDK + embedder + pgvector client + ~620 MB model) into the web tier. The Flask
# `app` object lives in genai.service.app; import it from there (e.g. gunicorn genai.service.app:app).
from genai.service.app import app

__all__ = ["app"]
