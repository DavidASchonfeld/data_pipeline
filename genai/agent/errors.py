# One error type per agent layer, so callers catch a single named exception instead of guessing at
# raw library errors — the same convention as LLMProviderError (llm) and RetrieverError (retrieval).


class AgentError(Exception):
    """Base error for the agent layer: tool failures, orchestration faults, and config problems
    all surface as this (or a subclass) so a caller never has to catch a grab-bag of library types."""
    pass


class SqlSafetyError(AgentError):
    """Raised when the SQL safety layer refuses a query — anything that is not a plain SELECT against
    the allowlisted MARTS/ANALYTICS schemas. Refusing here is a feature, not a bug (it blocks
    'drop the financials table' and friends), so the orchestrator feeds it back to the model rather
    than crashing."""
    pass


class CostCapExceeded(AgentError):
    """Internal signal that running the next LLM turn would push a single question over
    GENAI_MAX_COST_PER_QUERY. The orchestrator catches this to finalize gracefully with whatever it
    has gathered, rather than spend past the budget."""
    pass
