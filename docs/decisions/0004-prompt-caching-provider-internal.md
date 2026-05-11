# ADR 0004: Prompt Caching Is Provider-Internal

- Status: Accepted
- Date: 2026-05-10

## Context

AI language models charge per token — each word (roughly) costs a small fraction of a cent. When sending the same long instructions to the AI repeatedly (for example, a detailed extraction prompt sent once per company filing), caching the instructions on the server side can cut costs significantly.

Different AI services handle caching very differently:

- **Anthropic:** Opt-in. You annotate specific parts of your request with `cache_control: {"type": "ephemeral"}`. The server caches that part for about five minutes and charges a reduced rate on cache hits.
- **OpenAI:** Automatic. Requests longer than 1 024 tokens are cached without any annotation from the caller. You get the discount automatically.
- **Ollama (local models):** No server-side cache at all. The model runs locally and each request is independent.

## Decision

Caching is **not** part of the `LLMProvider` interface (see ADR 0002). There is no `enable_cache=True` parameter on `complete()` or `chat()`.

Instead, each provider applies its own native caching mechanism internally, invisible to callers. For `AnthropicProvider`, the `chat()` method checks whether the system prompt is long enough to benefit from caching (roughly 1 024 tokens, estimated as 4 096 characters) and adds the `cache_control` annotation automatically. Callers do not need to know this happens.

When an OpenAI provider is added later, it will do nothing for caching — because OpenAI caches automatically, doing nothing is the correct behaviour. When an Ollama provider is added, it will also do nothing — because no cache exists.

## Alternatives considered

**Add `enable_cache: bool = False` to the `LLMProvider` interface.** The caller would write `provider.chat(messages, enable_cache=True)` when they want caching. This seems clean but is misleading: for OpenAI the flag does nothing (caching is always on), and for Ollama it also does nothing (caching is not available). A flag that means different things for different providers, or nothing at all, adds confusion without adding control.

**Add `cache_hint(text: str) -> str` to the interface.** Callers would wrap static text in this helper, and each provider would translate the hint into its native mechanism. More honest than a boolean flag, but adds API surface area before there is evidence that any caller needs it. The same outcome — caching long system prompts — can be achieved provider-internally with no extra interface.

**Never cache at all.** Simple. But for EPIC 4 (structured extraction, many filings, same prompt each time), caching on Anthropic can reduce API costs by 80–90% for repeated runs. Skipping it would be wasteful.

## Consequences

**Wins:**
- Callers are completely unaware of caching. The same `provider.chat(...)` call gets caching benefits on Anthropic and auto-caching on OpenAI with zero changes to calling code.
- Adding a new provider never forces a decision about what a generic `enable_cache` flag means for that provider.

**Trade-offs:**
- Developers reading `AnthropicProvider` need to understand why `cache_control` is added there and not in the interface. This ADR is the explanation.
- If a caller ever needs fine-grained control over what gets cached (e.g., cache the instructions but not the data), there is no current mechanism for that. That use case would need a new interface addition at that time.
