from genai.config import LLM_PROVIDER
from genai.llm.base import LLMProvider

# _SUPPORTED lists every valid value for LLM_PROVIDER so the error message is always up to date
_SUPPORTED: list[str] = ["anthropic", "openai"]


def get_llm_provider() -> LLMProvider:
    # look up the chosen provider and return a ready-to-use instance — callers never import a concrete class directly
    provider = LLM_PROVIDER.lower()

    if provider == "anthropic":
        # import inside the function so the SDK is only loaded when this provider is actually requested
        from genai.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    if provider == "openai":
        from genai.llm.openai_provider import OpenAIProvider
        return OpenAIProvider()

    # unknown provider — tell the user exactly what values are accepted
    raise ValueError(
        f"Unknown LLM provider: '{provider}'. "
        f"Supported values for LLM_PROVIDER: {_SUPPORTED}. "
        "To add a new provider, create genai/llm/<name>_provider.py and add a branch here."
    )
