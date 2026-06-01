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

    # build_tool_turn: given a normalised chat() response that asked to call tools, plus the string
    # outputs of running those tools (keyed by tool-call id), return the messages to APPEND to the
    # conversation so the next chat() turn carries the results back to the model. The wire format
    # differs per provider, so this lives here (with an Anthropic-shaped default) and each provider
    # overrides it as needed — the agent orchestrator stays provider-agnostic and never touches
    # provider-specific message shapes itself.
    def build_tool_turn(self, response: dict, tool_outputs: dict[str, str]) -> list[dict]:
        # Default = Anthropic's content-block shape. Reconstruct the assistant turn (any text, then a
        # tool_use block per call) from the normalised response, then a user turn with one
        # tool_result per call referencing its tool_use id. Anthropic requires every tool_use to have
        # a matching tool_result, in this assistant-then-user order.
        assistant_blocks: list[dict] = []
        if response.get("content"):
            assistant_blocks.append({"type": "text", "text": response["content"]})
        for call in response.get("tool_calls", []):
            assistant_blocks.append({
                "type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"],
            })
        result_blocks = [
            {"type": "tool_result", "tool_use_id": call["id"], "content": tool_outputs.get(call["id"], "")}
            for call in response.get("tool_calls", [])
        ]
        return [
            {"role": "assistant", "content": assistant_blocks},
            {"role": "user", "content": result_blocks},
        ]
