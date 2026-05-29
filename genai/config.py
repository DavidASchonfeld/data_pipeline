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

# How long (seconds) to wait on a single LLM request before giving up. The SDK default is
# ~10 minutes, far too long for a dashboard or a DAG task — cap it so a stuck call fails fast.
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))

# How many times the SDK retries a transient failure (429 rate-limit, 5xx, dropped connection)
# before surfacing the error. The Anthropic/OpenAI SDKs handle the exponential backoff + jitter
# internally, so we only set the attempt count — we never add a second retry layer on top.
LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "3"))

# ── pgvector connection ───────────────────────────────────────────────────────
# pgvector is a Postgres database that stores text embeddings (384-number "meaning fingerprints").
# Future EPICs write filing chunks and weather summaries here; EPIC 8 queries it for semantic search.
#
# These values are overridden at runtime by the pgvector-credentials K8s secret (applied by deploy.sh
# step 2c2d when GENAI_ENABLED=true). The defaults here point to the in-cluster service DNS name so
# local dev works without env-var overrides once the pod is running.

# Internal hostname of the pgvector pod — set by the K8s Service in service-pgvector.yaml
PGVECTOR_HOST: str = os.environ.get("PGVECTOR_HOST", "pgvector.airflow-my-namespace.svc.cluster.local")

# Postgres login credentials — must match POSTGRES_USER/PASSWORD/DB in pgvector-secret.yaml
PGVECTOR_USER: str = os.environ.get("PGVECTOR_USER", "pgvector")
PGVECTOR_PASSWORD: str = os.environ.get("PGVECTOR_PASSWORD", "")   # empty default — must be set via secret
PGVECTOR_DB: str = os.environ.get("PGVECTOR_DB", "pgvector")

# ── Cost guardrails ───────────────────────────────────────────────────────────
# Maximum cost (in USD) allowed for a single AI query — enforced in EPIC 9's orchestrator.
# Read here so every future epic can reference a single source of truth.
GENAI_MAX_COST_PER_QUERY: float = float(os.environ.get("GENAI_MAX_COST_PER_QUERY", "0.05"))

# Maximum total AI spend (in USD) allowed per day — also enforced in EPIC 9.
GENAI_DAILY_BUDGET: float = float(os.environ.get("GENAI_DAILY_BUDGET", "2.00"))
