from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from genai.config import LLM_API_KEY, LLM_MAX_RETRIES, LLM_MODEL, LLM_TIMEOUT_SECONDS
from genai.llm.base import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    # types only — pulled in for the type checker, never imported at runtime, so the SDK stays deferred
    from openai.types.chat import ChatCompletion

# Module logger — records calls and failures without ever logging prompt/response text (no PII).
logger = logging.getLogger(__name__)


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

        # create a reusable client so we don't open a new HTTP connection for every request;
        # cap the per-request timeout and let the SDK handle backoff/jitter for the retry count
        self._client = openai.OpenAI(
            api_key=LLM_API_KEY,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )
        self._model = LLM_MODEL

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        # complete: wrap the prompt as a single user message and return just the text reply
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # SDK errors (rate limit, timeout, auth) become one error type
            logger.error("OpenAI complete() failed for model %s: %s", self._model, exc)
            raise LLMProviderError(f"OpenAI request failed (model={self._model}): {exc}") from exc

        # an empty choices list means an unexpected response shape — fail with a clear message
        if not response.choices:
            raise LLMProviderError(f"OpenAI returned no choices (model={self._model})")
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

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # SDK errors (rate limit, timeout, auth) become one error type
            logger.error("OpenAI chat() failed for model %s: %s", self._model, exc)
            raise LLMProviderError(f"OpenAI request failed (model={self._model}): {exc}") from exc
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

    def _normalise_response(self, response: ChatCompletion) -> dict:
        # extract text and tool-call blocks from the response into the same dict shape all providers return
        if not response.choices:
            raise LLMProviderError(f"OpenAI returned no choices (model={self._model})")
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                # only function tool calls carry name/arguments — skip any other tool-call variant the API may return
                if tc.type != "function":
                    # I only register function tools, so this shouldn't happen — log it instead of dropping silently
                    logger.warning(
                        "OpenAI returned an unsupported tool-call type %r; skipping (model=%s)",
                        tc.type, self._model,
                    )
                    continue
                # OpenAI returns tool arguments as a JSON string — parse it into a dict
                tool_calls.append({
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments),
                    "id": tc.id,
                })

        # usage can be absent on some responses — fall back to 0 so the dict shape stays consistent for callers
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        logger.debug(
            "OpenAI chat() ok: model=%s in=%s out=%s tool_calls=%s",
            self._model, input_tokens, output_tokens, len(tool_calls),
        )
        return {
            "content": message.content or "",
            "stop_reason": choice.finish_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "tool_calls": tool_calls,
        }
