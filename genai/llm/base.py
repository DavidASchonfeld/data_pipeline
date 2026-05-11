from abc import ABC, abstractmethod

# LLMProvider: a contract (interface) that every AI provider must fulfil.
# Adding a new provider means creating a new file and implementing these two methods.


class LLMProvider(ABC):
    # complete: send a single block of text and get a text reply back (simplest use case)
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 1024) -> str: ...

    # chat: send a conversation history (list of messages) and optionally describe tools the AI can call
    # Returns a dict with keys: content (str), stop_reason (str), usage (dict), tool_calls (list)
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> dict: ...
