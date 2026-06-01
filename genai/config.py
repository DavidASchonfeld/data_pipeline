import os

# python-dotenv is a local-dev convenience that loads a .env file. It is OPTIONAL: in production
# every value already comes from the environment / K8s secrets, so load_dotenv() is a no-op there.
# Importing it defensively keeps genai/ runnable under any venv — notably the ml-venv subprocess
# runner, which has no reason to carry a dev-only dependency.
try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env for local dev; no-op in production
except ModuleNotFoundError:
    pass

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

# ── Embedding provider (EPIC 6) ─────────────────────────────────────────────────
# Which service turns text into a vector (a 384-number "meaning fingerprint"). "local" runs the
# sentence-transformers model on this server — free, no API key, no data leaves the box. Swap to a
# hosted provider later by adding genai/embedding/<name>_embedder.py and a branch in _factory.py.
EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "local")

# The specific local embedding model. all-MiniLM-L6-v2 is small (~80 MB), 384-dimensional, and
# Apache-2.0 licensed — a solid default for semantic search. Override only if you also rebuild the
# pgvector table to match the new model's dimension.
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# ── SEC EDGAR full-text fetch ──────────────────────────────────────────────────
# Used by genai/extraction/edgar_fulltext.py to download 10-K filings. SEC EDGAR is free,
# public-domain U.S. government data — no API key — but requires a descriptive User-Agent with
# contact info (SEC blocks anonymous callers) and fair-access rate limits.

# Contact email baked into the User-Agent so SEC can reach me if a script misbehaves.
EDGAR_CONTACT_EMAIL: str = os.environ.get("EDGAR_CONTACT_EMAIL", "contact@stocklivedata.dev")

# How long (seconds) to wait on a single EDGAR HTTP request before giving up — fail fast.
EDGAR_TIMEOUT_SECONDS: float = float(os.environ.get("EDGAR_TIMEOUT_SECONDS", "30"))

# How many times to retry a transient EDGAR failure (429 rate-limit, 5xx, dropped connection).
# The retry/backoff is handled once by the requests Session adapter — no second hand-rolled loop.
EDGAR_MAX_RETRIES: int = int(os.environ.get("EDGAR_MAX_RETRIES", "3"))

# ── Structured extraction (EPIC 4) ─────────────────────────────────────────────
# Used by genai/runners/extract_runner.py to pull structured facts out of 10-K sections.
# These caps keep one extraction run cheap and predictable (target under $0.50 per run).

# Largest LLM reply (in tokens) allowed per extraction call — a list of risks or guidance
# statements never needs the full 2048 default, so capping it lower trims output cost.
GENAI_EXTRACT_MAX_TOKENS: int = int(os.environ.get("GENAI_EXTRACT_MAX_TOKENS", "1500"))

# Largest slice of a 10-K section (in characters) sent to the LLM. Item 1A "Risk Factors" can
# run 50k+ characters; trimming to ~24k (≈6k tokens) keeps input cost bounded — the runner adds
# a visible truncation marker so a clipped section is obvious in the prompt.
GENAI_MAX_SECTION_CHARS: int = int(os.environ.get("GENAI_MAX_SECTION_CHARS", "24000"))

# ── Weather summary (EPIC 5) ───────────────────────────────────────────────────
# Largest LLM reply (in tokens) allowed per weather-summary call. A 2–4 sentence summary needs far
# less than the 2048 default, so capping it low trims output cost across the ~10 cities per run.
GENAI_WEATHER_MAX_TOKENS: int = int(os.environ.get("GENAI_WEATHER_MAX_TOKENS", "400"))

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

# ── RAG ingest (EPIC 7) ─────────────────────────────────────────────────────────
# How the ingest DAG slices long filing text before turning each slice into a vector. A "token" is
# roughly a word-piece; the chunker approximates tokens from word count (~0.75 words/token) so no
# tokenizer dependency is needed. ~500-token chunks with a 50-token overlap is a common RAG default —
# big enough to hold context, small enough to retrieve precisely; the overlap avoids cutting a fact in half.
GENAI_CHUNK_TARGET_TOKENS: int = int(os.environ.get("GENAI_CHUNK_TARGET_TOKENS", "500"))
GENAI_CHUNK_OVERLAP_TOKENS: int = int(os.environ.get("GENAI_CHUNK_OVERLAP_TOKENS", "50"))

# How many text chunks to embed per batch. Lowered from 32 to 16 for RAM headroom: the first
# production ingest peaked at 1446Mi in the scheduler pod (under the 1.5 GB cap, but tight), and a
# smaller batch shrinks the per-batch tensor memory at a negligible throughput cost. Raise it again
# only if a future profile shows comfortable headroom.
GENAI_EMBED_BATCH_SIZE: int = int(os.environ.get("GENAI_EMBED_BATCH_SIZE", "16"))

# ── Cost guardrails ───────────────────────────────────────────────────────────
# Maximum cost (in USD) allowed for a single AI query — enforced in EPIC 9's orchestrator.
# Read here so every future epic can reference a single source of truth.
GENAI_MAX_COST_PER_QUERY: float = float(os.environ.get("GENAI_MAX_COST_PER_QUERY", "0.05"))

# Maximum total AI spend (in USD) allowed per day — also enforced in EPIC 9.
GENAI_DAILY_BUDGET: float = float(os.environ.get("GENAI_DAILY_BUDGET", "2.00"))
