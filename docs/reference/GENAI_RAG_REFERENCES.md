# GenAI / RAG — External References

> Curated, annotated links behind the GenAI layer's design choices. Each entry is a one-line summary **in my own words** plus a link to the original — no external text or source code is copied into this repo (per `CLAUDE.md`). These are design references for reading and learning, not dependencies. If any of them leads to adopting a new library, that library must first be recorded in `THIRD_PARTY_NOTICES.md` with its license.
>
> Companion to [ADR 0005](../decisions/0005-source-agnostic-ingestion.md) (which lists the source-agnostic-ingestion sources) and [GENAI_DATA_SEPARATION](../architecture/GENAI_DATA_SEPARATION.md). This file collects the *production-RAG best-practice gaps* found while reviewing whether anything was missing from the roadmap.

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

## How to use this file

- When implementing a roadmap "Best-practice gap," read the matching section here first.
- When adding a new source or topic, read [GENAI_DATA_SEPARATION](../architecture/GENAI_DATA_SEPARATION.md).
- Keep this list trimmed — a link that no longer informs a decision should be removed, not left to rot.
