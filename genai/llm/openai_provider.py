import json

from genai.config import LLM_API_KEY, LLM_MODEL
from genai.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    # __init__: lazy-load the SDK so importing this file never fails when openai is not installed
    def __init__(self) -> None:
        try:
            import openai  # deferred import — not loaded unless this provider is actually requested
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is not installed. "
                "Run: pip install openai  (or check that the Airflow image was rebuilt after adding it to the Dockerfile)"
            ) from exc

        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY is not set. Add it to .env.deploy or the K8s genai-credentials secret.")

        # create a reusable client so we don't open a new HTTP connection for every request
        self._client = openai.OpenAI(api_key=LLM_API_KEY)
        self._model = LLM_MODEL

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        # complete: wrap the prompt as a single user message and return just the text reply
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> dict:
        # chat: send a multi-turn conversation and optionally let the AI call tools

        # OpenAI takes the system prompt as the first message in the list, not a separate parameter
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        kwargs: dict = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=full_messages,
        )
        if tools:
            kwargs["tools"] = self._translate_tools(tools)

        response = self._client.chat.completions.create(**kwargs)
        return self._normalise_response(response)

    def _translate_tools(self, tools: list[dict]) -> list[dict]:
        # convert the generic tool spec format into the structure OpenAI's API expects
        translated = []
        for tool in tools:
            translated.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return translated

    def _normalise_response(self, response: object) -> dict:
        # extract text and tool-call blocks from the response into the same dict shape all providers return
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                # OpenAI returns tool arguments as a JSON string — parse it into a dict
                tool_calls.append({
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments),
                    "id": tc.id,
                })

        return {
            "content": message.content or "",
            "stop_reason": choice.finish_reason,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
            "tool_calls": tool_calls,
        }
