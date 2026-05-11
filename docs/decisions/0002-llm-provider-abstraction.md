# ADR 0002: LLM Provider Abstraction (Swappable Backends)

- Status: Accepted
- Date: 2026-05-10

## Context

The pipeline calls an AI language model (LLM) to extract structured data from filings, write weather summaries, and answer questions in the dashboard chat. Many AI services exist: Anthropic, OpenAI, Google Gemini, and local models like Ollama.

If the code called Anthropic's SDK directly everywhere — `import anthropic; anthropic.Anthropic(...).messages.create(...)` — then switching to a different service later would mean hunting through dozens of files and rewriting each one. It would also mean that loading any Python file in the project would trigger the `import anthropic` line, even if the AI layer was turned off.

## Decision

I designed the system around a single interface called `LLMProvider`. It is a contract (in Python terms, an abstract base class) that defines exactly two methods:

- `complete(prompt, max_tokens)` — send a text question, get a text answer back.
- `chat(messages, tools, system, max_tokens)` — send a conversation and optionally let the AI call tools.

Any AI service that wants to work with this pipeline must implement both methods. The rest of the codebase calls only these two methods and never knows which service is underneath.

A factory function, `get_llm_provider()`, reads the `LLM_PROVIDER` environment variable and returns the correct implementation. Callers import `get_llm_provider` and call it — they never import `anthropic`, `openai`, or any other SDK directly.

Currently one implementation exists: `AnthropicProvider`. The `anthropic` SDK is only imported inside that file, and only when the factory actually instantiates it. If `GENAI_ENABLED=false`, the import never happens.

To add a new AI service later:
1. Create `genai/llm/openai_provider.py` (or any name).
2. Add one branch to the factory function.
3. Set `LLM_PROVIDER=openai` in `.env.deploy`.
4. No other file changes are needed.

## Alternatives considered

**Direct SDK imports everywhere.** Simplest to write initially. Becomes expensive to maintain when switching providers, and couples every file to a specific vendor.

**A single config dict with provider-specific keys.** For example, `{"provider": "anthropic", "api_key": "...", "model": "..."}`. Avoids the abstract class but still requires callers to know which keys apply to which provider.

**A third-party abstraction library (LiteLLM, LangChain).** These exist and handle multi-provider routing. I chose not to use them because they add a large dependency with its own API surface, version constraints, and behaviour I do not fully control. A thin custom interface costs very little to write and is much easier to reason about.

## Consequences

**Wins:**
- Switching AI providers is an environment-variable change plus one new file. Nothing else changes.
- The Anthropic SDK is never imported when `GENAI_ENABLED=false`, so there is no risk of startup failures.
- The interface is easy to test: replace `get_llm_provider()` with a fake implementation that returns canned responses, and all downstream code is testable without a real API key.

**Trade-offs:**
- Provider-specific features (batch processing, streaming, fine-tuning) are not exposed through the interface. Adding a feature that only one provider supports requires either extending the interface for everyone or bypassing it.
- The normalised response format (a plain dict) is a least-common-denominator design. Provider-specific metadata (e.g., Anthropic's exact token counts) is still included, but the structure must work for all providers.
