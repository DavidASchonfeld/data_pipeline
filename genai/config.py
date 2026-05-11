import os

from dotenv import load_dotenv  # reads .env for local dev; no-op in production

load_dotenv()

# ── Feature flag ──────────────────────────────────────────────────────────────
# Think of this as a light switch: when it's off, the AI layer does not run at all.
# Local dev:   set GENAI_ENABLED=true in a .env file at the repo root (gitignored)
# Production:  set GENAI_ENABLED=true in .env.deploy before running deploy.sh
GENAI_ENABLED: bool = os.environ.get("GENAI_ENABLED", "false").lower() == "true"

# ── LLM provider ─────────────────────────────────────────────────────────────
# Which AI text-generation service to use. "anthropic" uses the Anthropic API.
# Swap to "openai" or "ollama" by adding a new provider file and changing this value.
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "anthropic")

# The specific AI model to use within the chosen provider.
# "claude-sonnet-4-5" is a fast, affordable model — good for most extraction tasks.
LLM_MODEL: str = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")

# The secret API key that authenticates requests to the LLM provider.
# Never hard-code this here — it must come from the environment or a K8s secret.
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")

# ── Cost guardrails ───────────────────────────────────────────────────────────
# Maximum cost (in USD) allowed for a single AI query — enforced in EPIC 9's orchestrator.
# Read here so every future epic can reference a single source of truth.
GENAI_MAX_COST_PER_QUERY: float = float(os.environ.get("GENAI_MAX_COST_PER_QUERY", "0.05"))

# Maximum total AI spend (in USD) allowed per day — also enforced in EPIC 9.
GENAI_DAILY_BUDGET: float = float(os.environ.get("GENAI_DAILY_BUDGET", "2.00"))
