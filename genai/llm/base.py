from abc import ABC, abstractmethod


# LLMProviderError: one error type every provider raises, so callers (e.g. the EPIC 9
# orchestrator) catch a single exception regardless of which backend is active. Wraps the
# raw SDK exceptions and any malformed-response surprises with the model name for context.
class LLMProviderError(Exception):
    pass


# LLMProvider: a contract (interface) that every AI provider must fulfil.
# Adding a new provider means creating a new file and implementing these two methods.


class LLMProvider(ABC):
    # complete: send a single block of text and get a text reply back (simplest use case)
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 1024) -> str: ...

    # chat: send a conversation history (list of messages) and optionally describe tools the AI can call
    # Returns a dict with keys: content (str), stop_reason (str), usage (dict), tool_calls (list), model (str)
    #   temperature: sampling randomness; pass 0 for deterministic, reproducible output (extraction). None = SDK default.
    #   tool_choice: a provider-neutral TOOL-NAME string that FORCES the model to call that tool — each
    #                provider translates it into its own format. None = the model decides whether to call a tool.
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float | None = None,
        tool_choice: str | None = None,
    ) -> dict: ...
