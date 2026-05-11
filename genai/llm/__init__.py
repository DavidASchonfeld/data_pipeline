# llm: public entry point — import get_llm_provider() from here, never from a concrete provider file
from genai.llm._factory import get_llm_provider
from genai.llm.base import LLMProvider

__all__ = ["get_llm_provider", "LLMProvider"]
