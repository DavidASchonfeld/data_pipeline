# embedding: public entry point — import get_embedder() from here, never from a concrete backend file
from genai.embedding._factory import get_embedder
from genai.embedding.base import Embedder, EmbedderError

__all__ = ["get_embedder", "Embedder", "EmbedderError"]
