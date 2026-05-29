from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from genai.config import LLM_API_KEY, LLM_MAX_RETRIES, LLM_MODEL, LLM_TIMEOUT_SECONDS
from genai.llm.base import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    # types only — pulled in for the type checker, never imported at runtime, so the SDK stays deferred
    from anthropic.types import Message

# Module logger — records calls and failures without ever logging prompt/response text (no PII).
logger = logging.getLogger(__name__)

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

        # create a reusable client so we don't open a new HTTP connection for every request;
        # cap the per-request timeout and let the SDK handle backoff/jitter for the retry count
        self._client = anthropic.Anthropic(
            api_key=LLM_API_KEY,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )
        self._model = LLM_MODEL

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        # complete: wrap the prompt as a single user message and return just the text reply
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # SDK errors (rate limit, timeout, auth) become one error type
            logger.error("Anthropic complete() failed for model %s: %s", self._model, exc)
            raise LLMProviderError(f"Anthropic request failed (model={self._model}): {exc}") from exc

        # guard against an empty/text-less response before indexing, so callers get a clear error
        for block in response.content:
            if block.type == "text":
                logger.debug(
                    "Anthropic complete() ok: model=%s in=%s out=%s",
                    self._model, response.usage.input_tokens, response.usage.output_tokens,
                )
                return block.text
        raise LLMProviderError(
            f"Anthropic returned no text content (model={self._model}, stop_reason={response.stop_reason})"
        )

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

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:  # SDK errors (rate limit, timeout, auth) become one error type
            logger.error("Anthropic chat() failed for model %s: %s", self._model, exc)
            raise LLMProviderError(f"Anthropic request failed (model={self._model}): {exc}") from exc

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

    def _normalise_response(self, response: Message) -> dict:
        # extract text and tool-call blocks from the response into a consistent dict shape
        text_content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input, "id": block.id})
            else:
                # I only consume text and tool_use; thinking/server-tool blocks aren't enabled, so flag any surprise
                logger.warning(
                    "Anthropic returned an unhandled content block type %r; skipping (model=%s)",
                    block.type, self._model,
                )

        logger.debug(
            "Anthropic chat() ok: model=%s in=%s out=%s tool_calls=%s",
            self._model, response.usage.input_tokens, response.usage.output_tokens, len(tool_calls),
        )
        return {
            "content": text_content,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "tool_calls": tool_calls,
        }
