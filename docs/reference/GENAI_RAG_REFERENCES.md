# GenAI / RAG — External References

> Curated, annotated links behind the GenAI layer's design choices. Each entry is a one-line summary **in my own words** plus a link to the original — no external text or source code is copied into this repo (per `CLAUDE.md`). These are design references for reading and learning, not dependencies. If any of them leads to adopting a new library, that library must first be recorded in `THIRD_PARTY_NOTICES.md` with its license.
>
> Companion to [ADR 0005](../decisions/0005-source-agnostic-ingestion.md) (which lists the source-agnostic-ingestion sources) and [GENAI_DATA_SEPARATION](../architecture/GENAI_DATA_SEPARATION.md). Sections §1–§6 collect the *production-RAG best-practice gaps* found while reviewing the roadmap; §7–§11 (added 2026-05-29) extend it to the broader *LLM-ops* concerns — client robustness, security, cost, structured output, and observability — that apply across EPICs 1, 4, 9, and 11.

---

## Production-RAG best-practice gaps (found 2026-05-28)

Each section maps to a gap folded into the roadmap's "Best-practice gaps" list. Read these to understand *why* each item is there.

### 1. Deletions / tombstones / stale chunks

The risk: incremental sync that only adds and updates leaves "orphaned vectors" when source rows are deleted or regenerated — stale chunks keep getting retrieved and answered from with full confidence. Fix with stable doc/chunk IDs, content hashing for change detection, and an explicit deletion pass.

- [Building Production RAG: Architecture, Chunking, Evaluation & Monitoring (2026 Guide) — Prem AI](https://blog.premai.io/building-production-rag-architecture-chunking-evaluation-monitoring-2026-guide/)
- [RAG at Scale: How to Build Production AI Systems in 2026 — Redis](https://redis.io/blog/rag-at-scale/)
- [Common Challenges in RAG and How to Solve Them in Production — Unstructured](https://unstructured.io/insights/rag-pipeline-challenges-from-data-ingestion-to-retrieval)
- [What Matters in Production RAG — Arpit Bhayani](https://arpitbhayani.me/blogs/rag-production/)

### 2. Embedding model versioning & re-embedding migrations

The risk: swapping the embedder invalidates every stored vector. Stamp `model_name` / `model_version` / `dim` (and a `source_hash`) into metadata so you can re-embed only stale rows, run old/new side-by-side, and skip re-embedding unchanged text. Always keep raw source recoverable — vectors are derived data.

- [Embedding Models in Production: Selection, Versioning, and the Index Drift Problem — TianPan.co](https://tianpan.co/blog/2026-04-09-embedding-models-production-versioning-index-drift)
- [Embedding Portability and Versioning — Mixpeek](https://mixpeek.com/guides/embedding-portability-versioning)
- [RAG Series — Embedding Versioning with pgvector — dbi-services](https://www.dbi-services.com/blog/rag-series-embedding-versioning-with-pgvector-why-event-driven-architecture-is-a-precondition-to-ai-data-workflows/)
- [Drift-Adapter: Near Zero-Downtime Embedding Model Upgrades (arXiv paper)](https://arxiv.org/pdf/2509.23471)
- [Migrating Vector Databases: Moving Embeddings Between Pinecone, pgvector, Weaviate — CallSphere](https://callsphere.ai/blog/migrating-vector-databases-pinecone-pgvector-weaviate-embeddings)

### 3. Chunking strategy per document type (hierarchical / recursive)

The risk: one fixed chunk size for every source. A 300-page 10-K and a two-sentence weather summary need different handling. Best practice is recursive/hierarchical chunking at natural boundaries, with size chosen per document type; very short documents should not be chunked at all.

- [Chunking Strategies for RAG: Methods, Trade-offs & Best Practices — Atlan](https://atlan.com/know/chunking-strategies-rag/)
- [Best Chunking Strategies for RAG (and LLMs) in 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)
- [The Ultimate Guide to Chunking Strategies for RAG — Databricks Community](https://community.databricks.com/t5/technical-blog/the-ultimate-guide-to-chunking-strategies-for-rag-applications/ba-p/113089)
- [Chunking Strategies for RAG: Best Practices — Unstructured](https://unstructured.io/blog/chunking-for-rag-best-practices)

### 4. Evaluation: retrieval metrics vs generation metrics

The risk: only grading the final answer hides whether failures come from bad retrieval or bad generation. Evaluate them separately — retrieval (recall@k, precision@k, MRR, nDCG against expected sources) and generation (faithfulness, answer relevancy, citation coverage) — before end-to-end.

- [RAG Evaluation: 2026 Metrics and Benchmarks — Label Your Data](https://labelyourdata.com/articles/llm-fine-tuning/rag-evaluation)
- [RAG evaluation — Anyscale Docs](https://docs.anyscale.com/rag/evaluation)
- [RAG Evaluation: Metrics, Methods, and Benchmarks That Matter — Statsig](https://www.statsig.com/perspectives/rag-evaluation-metrics-methods-benchmarks)
- [Best Practices in RAG Evaluation — Qdrant](https://qdrant.tech/blog/rag-evaluation-guide/)

### 5. Observability / tracing & RAG anti-patterns

The risk: every RAG layer fails silently (bad chunk size shifts recall with no error; a missing self-check ships a hallucination). Persist the agent trace + scores (reuse MLflow — do not buy a platform). Beware trace anti-patterns: no PII in spans, avoid cardinality explosion.

- [RAG Anti-Patterns: 7 Failure Modes (2026 Engineering Guide) — Digital Applied](https://www.digitalapplied.com/blog/rag-anti-patterns-7-failure-modes-2026-engineering-guide)
- [Production RAG: From Anti-Patterns to Platform Engineering — Towards AI](https://pub.towardsai.net/rag-systems-anti-patterns-and-design-patterns-for-production-48b7d86c4bbd)
- [Agentic RAG in 2026: Patterns, Code, Observability — Future AGI](https://futureagi.com/blog/agentic-rag-systems-2025/)

### 6. Multi-tenant isolation & access control (porting caution only)

Relevant only if this tool is ported to multi-user or sensitive data. Namespace/metadata filtering is **not** a security boundary by itself — a forgotten filter leaks data. Use pre-retrieval authorization or Postgres Row-Level Security (RLS) in that case. Not needed for this project (single public dashboard, non-sensitive data).

- [Design a Secure Multitenant RAG Inferencing Solution — Microsoft Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/secure-multitenant-rag)
- [Building Multi-Tenant RAG Applications with PostgreSQL — Tiger Data](https://www.tigerdata.com/blog/building-multi-tenant-rag-applications-with-postgresql-choosing-the-right-approach)
- [RAG with Permissions — Supabase Docs](https://supabase.com/docs/guides/ai/rag-with-permissions)
- [Implementing Row-Level Security in Vector DBs for RAG — Michael Hannecke (Medium)](https://medium.com/@michael.hannecke/implementing-row-level-security-in-vector-dbs-for-rag-applications-fdbccb63d464)

---

## LLM-ops best-practice gaps (found 2026-05-29)

These extend the review beyond retrieval to the LLM-client and agent layers. None requires a new dependency for this project; the SDK-native paths cover them.

### 7. LLM client robustness: retries, timeouts, rate-limit (429/529) handling

The risk: a single transient API error (rate limit, overload, dropped connection) crashes a DAG task or a chat request, and a missing timeout lets a stuck call hang for minutes. The official SDKs already retry these with exponential backoff + jitter — configure the attempt count and an explicit per-request timeout rather than hand-rolling a second retry loop on top. (Applied in EPIC 1: `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`.)

- [Anthropic Python SDK — request lifecycle & error handling (what auto-retries, and when) — DeepWiki](https://deepwiki.com/anthropics/anthropic-sdk-python/4.5-request-lifecycle-and-error-handling)
- [Handling Anthropic 429 / 529 rate-limit and overload errors in production — Respan](https://www.respan.ai/articles/anthropic-api-rate-limits)
- [Anthropic prompt caching — official docs (cache breakpoints, 5-min/1-hr TTL, tool caching) — Claude API](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Tenacity — general-purpose Python retry library (reference only; the SDK's own retries make it unnecessary here)](https://tenacity.readthedocs.io/)

### 8. LLM application security (OWASP Top 10 for LLM Applications, 2025)

The risk: retrieved passages and model output are untrusted. Prompt injection (LLM01) can hide instructions inside a filing; improper output handling (LLM05) means rendering model output without sanitizing it (XSS); excessive agency (LLM06) and unbounded consumption (LLM10) are the agent/cost failure modes. Delimit retrieved text, never let it override the system prompt, sanitize before rendering (reuse `dashboard/security.py`), and bound inputs/turns/spend. (Folds into EPIC 9 + 10.)

- [LLM01:2025 Prompt Injection — OWASP GenAI Security Project](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [OWASP Top 10 for LLM Applications — project home (all ten risks)](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [OWASP Top 10 for LLM Applications 2025 — full PDF](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)

### 9. Cost & token accounting / budget enforcement

The risk: a blended cost estimate over-/under-counts because cached input tokens are far cheaper, and without per-request attribution you can't see what drove a spend spike. Track input/output/cache-read tokens separately, tag each call with a request/feature id, compute cost from a per-model price map, and enforce the budget automatically (short-circuit, not manual). (Folds into EPIC 9's `FCT_LLM_USAGE` + Cross-Cutting cost guardrails.)

- [From Bills to Budgets: tracking LLM token usage and cost per user/feature — Traceloop](https://www.traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user)
- [Rate limiting for LLM applications: why and how — Portkey](https://portkey.ai/blog/rate-limiting-for-llm-applications/)
- [Attributing, budgeting, and capping agentic LLM costs — TrueFoundry](https://www.truefoundry.com/blog/llm-cost-attribution-agentic-cicd)

### 10. Structured outputs / extraction reliability

The risk: free-parsing JSON from a free-text completion fails unpredictably. Constrain the output with provider-native tool-calling / JSON-schema enforcement, validate against the Pydantic schema, and bound the retry-on-validation-failure (retries multiply cost). Put reasoning fields before answer fields, make uncertain fields optional to avoid forced hallucination, and use one schema per task. (Folds into EPIC 4.)

- [Retry-on-validation-failure with Instructor + Pydantic — Instructor docs](https://python.useinstructor.com/concepts/retrying/)
- [LLM structured outputs: schema validation for real pipelines — Collin Wilkins](https://collinwilkins.com/articles/structured-output)
- [Reliable JSON from any LLM (constrained decoding, field ordering, optional fields) — TECHSY](https://techsy.io/en/blog/llm-structured-outputs-guide)

### 11. LLM observability / tracing (OpenTelemetry GenAI semantic conventions)

The risk: home-grown trace attribute names don't line up with any tooling, so traces aren't portable. When persisting the agent trace to MLflow (already running), name attributes with the OpenTelemetry GenAI conventions (`gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.response.finish_reasons`). Pure naming convention — no new dependency. (Folds into EPIC 9/11.)

- [Semantic conventions for generative-AI client spans — OpenTelemetry spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [Inside the LLM call: GenAI observability with OpenTelemetry — OpenTelemetry blog](https://opentelemetry.io/blog/2026/genai-observability/)
- [OpenTelemetry GenAI semantic conventions in MLflow tracing — MLflow docs](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/genai-semconv/)

---

## How to use this file

- When implementing a roadmap "Best-practice gap," read the matching section here first.
- When adding a new source or topic, read [GENAI_DATA_SEPARATION](../architecture/GENAI_DATA_SEPARATION.md).
- Keep this list trimmed — a link that no longer informs a decision should be removed, not left to rot.
