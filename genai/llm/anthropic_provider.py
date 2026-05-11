from genai.config import LLM_API_KEY, LLM_MODEL
from genai.llm.base import LLMProvider

# Token threshold above which a system prompt qualifies for server-side caching.
# Anthropic caches prompts for ~5 minutes — saves money on repeated extraction tasks.
# 1024 tokens ≈ 4096 characters (rough estimate using the standard 4 chars-per-token rule).
_CACHE_CHAR_THRESHOLD = 4096


class AnthropicProvider(LLMProvider):
    # __init__: lazy-load the SDK so importing this file never fails when Anthropic is not installed
    def __init__(self) -> None:
        try:
            import anthropic  # deferred import — not loaded unless GENAI_ENABLED and this provider is used
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is not installed. "
                "Run: pip install anthropic  (or check that ml-venv was built with GENAI_ENABLED=true)"
            ) from exc

        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY is not set. Add it to .env.deploy or the K8s genai-credentials secret.")

        # create a reusable client so we don't open a new HTTP connection for every request
        self._client = anthropic.Anthropic(api_key=LLM_API_KEY)
        self._model = LLM_MODEL

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        # complete: wrap the prompt as a single user message and return just the text reply
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # extract the first text block from the response content list
        return response.content[0].text

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> dict:
        # chat: send a multi-turn conversation and optionally let the AI call tools

        # build the system parameter — apply caching when the system prompt is large enough to benefit
        system_param = self._build_system_param(system)

        kwargs: dict = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system_param:
            kwargs["system"] = system_param
        if tools:
            # translate generic tool specs into Anthropic's expected format
            kwargs["tools"] = self._translate_tools(tools)

        response = self._client.messages.create(**kwargs)

        # normalise the response into a format all callers can rely on regardless of provider
        return self._normalise_response(response)

    def _build_system_param(self, system: str | None) -> list[dict] | None:
        # return None when there's no system prompt, so we don't send an empty system block
        if not system:
            return None

        block: dict = {"type": "text", "text": system}

        # only mark for caching when the prompt is long enough — short prompts don't benefit
        if len(system) >= _CACHE_CHAR_THRESHOLD:
            block["cache_control"] = {"type": "ephemeral"}

        return [block]

    def _translate_tools(self, tools: list[dict]) -> list[dict]:
        # convert the generic tool spec format into the structure Anthropic's API expects
        translated = []
        for tool in tools:
            translated.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
            })
        return translated

    def _normalise_response(self, response: object) -> dict:
        # extract text and tool-call blocks from the response into a consistent dict shape
        text_content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input, "id": block.id})

        return {
            "content": text_content,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "tool_calls": tool_calls,
        }
