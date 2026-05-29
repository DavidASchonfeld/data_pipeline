# ADR 0005: Source-Agnostic Ingestion (Document + Reader Port)

- Status: Proposed
- Date: 2026-05-28

## Context

The GenAI layer was first scoped around two specific data sources: SEC EDGAR filings and Open-Meteo weather. The goal now is broader: make the layer **source-agnostic and future-proof**, so it can ingest "anything" later — different file types (PDF, Word, spreadsheets, images), different third-party APIs, content from the internet, local disks, even a plugged-in USB drive — without rewriting the engine each time.

The open question was *how far* to generalize, and *when*. The tempting answer ("build adapters for every possible source now") needed checking against industry best practice before committing, because the pgvector database is still empty (changing its shape is free today, a migration later).

This ADR records what the research found and the resulting decision. The research and its sources are listed at the bottom so they can be re-read.

## What the research said (summary)

1. **Everything normalizes to one shape: a "Document" = text + a bag of metadata.** This is the universal pattern across the mature frameworks. LlamaIndex calls its source adapters "Readers," and every Reader — whether for a PDF, a Google Doc, a Slack message, or a webpage — outputs the same `Document` object. Once the input is always "a stream of Documents," the chunk → embed → store → retrieve → answer engine never has to know where the data came from.

2. **The formal name for this design is "Ports and Adapters" (Hexagonal Architecture).** The core defines a *port* (an abstract interface); each data source is an *adapter* implementing that port. This is the same pattern already used in this project for `LLMProvider` and `Embedder` (see [[0002-llm-provider-abstraction]]).

3. **"Future-proof for every file type" does NOT mean hand-writing a parser for every format.** Best practice is to **delegate parsing to a mature library**. The `unstructured` library alone handles 64+ file types behind a single `partition()` call; LlamaIndex's `SimpleDirectoryReader` covers many common formats. So the way to support "everything" is to wrap one of these behind the Reader port — not to write and maintain dozens of parsers myself (which I could never finish anyway).

4. **Building all adapters up front is an anti-pattern.** The hexagonal-architecture literature is explicit: the extra adapter code is justified *only* when you actually have multiple/changing sources. Applied blindly, it produces "ten times the code that should actually have been needed." The correct future-proofing is to define the **port now** and add **adapters on demand**.

5. **The storage schema should be generic: one namespaced table + a JSONB metadata column.** This is the validated pgvector pattern for multi-source / multi-tenant systems — a new source becomes a new `namespace` value plus whatever it wants in `metadata`, with **zero new tables and zero DDL**. Add an HNSW index on the vector column and a GIN index on the metadata column.

## Decision

Adopt a **source-agnostic ingestion design** built on three pieces, while respecting the YAGNI boundary:

1. **A `DataSource` / Reader port.** Add `genai/sources/base.py` defining one interface (an `iter_documents()` method yielding `Document(text, metadata)` objects), mirroring the existing `LLMProvider` / `Embedder` pattern. Selection by env var, via a `get_source()` factory.

2. **Delegate parsing to a mature, permissively-licensed library** (e.g. `unstructured` and/or LlamaIndex readers) for file-type coverage, wrapped behind the port. This buys broad format support without hand-rolling parsers.

3. **A generic pgvector schema.** Replace the two domain tables (`filing_chunks`, `weather_summaries`) with a single namespaced collection table while the database is empty:

   ```sql
   chunks(
     id          BIGSERIAL PRIMARY KEY,
     namespace   TEXT  NOT NULL,        -- 'sec_filings' | 'weather' | 'my_pdfs' | ...
     external_id TEXT  NOT NULL,        -- natural key, e.g. 'AAPL|2023-09-30|item1a|3'
     text        TEXT  NOT NULL,
     embedding   vector(384),
     metadata    JSONB,                 -- source-specific fields, filterable at query time
     UNIQUE (namespace, external_id)
   );
   ```

**Boundary (the important half of the decision):** build the **port + the generic schema + only the SEC and weather adapters that this project actually uses** now. Do **not** pre-build file/USB/REST adapters that have no current use — add each one when a real need appears, as a thin new file. "Future-proof" lives in the *interface*, not in speculative adapters.

## Alternatives considered

- **Keep the two domain tables and write bespoke per-source code (the original implicit plan).** Rejected for the reuse goal: every new source would need its own table + DDL, which contradicts the project's plug-and-play first principle.
- **Build the full multi-source framework now, with file/REST/USB adapters up front.** Rejected as the documented hexagonal-overuse anti-pattern: large amounts of unused, untested adapter code to maintain before a second real use case exists. The generic *port* delivers the future-proofing; speculative *adapters* only add cost.
- **Leave everything as-is, revisit later.** Rejected only for the storage schema, because the empty database makes now the cheap moment to generalize it; after data is loaded it becomes a migration. (For the adapters, "revisit later" is in fact the chosen approach.)

## Consequences

**Wins**
- Adding a new data source = one new adapter file + a new `namespace`. No engine changes, no schema changes, no DDL.
- Broad file-type support comes from a library, not from code I have to write and maintain.
- The schema matches the dict-based `filters` already planned for the Epic 8 retriever (`metadata->>'key'`).
- Consistent with the existing provider/embedder abstractions, so the codebase has one repeated, learnable pattern.

**Trade-offs**
- The generic table loses strict typed columns (e.g. `filing_date DATE`) in favor of JSONB; mitigated with a GIN index and documented metadata keys per namespace.
- A new dependency (`unstructured` or similar) adds install weight to ml-venv; gated behind `GENAI_ENABLED` like the rest.
- One indirection layer (the port) for sources; justified here precisely because multiple sources are an explicit goal — the condition the literature says makes the pattern worth it.

## Licensing & intellectual-property note

This decision is to **depend on** third-party libraries via their public APIs (`pip install`), **not** to copy any of their source code into this repository. That is the normal, lawful way to use open-source software and is not copyright infringement.

- **LlamaIndex** — MIT license (permissive).
- **`unstructured` (open-source core)** — Apache 2.0 (permissive). Note: Unstructured also sells a hosted **Platform** product; this project uses the open-source library only, unless a paid plan is deliberately chosen. Verify the license of any optional model/parser extras before relying on them.
- **pgvector** — PostgreSQL License (permissive); **sentence-transformers**, **rank-bm25** — Apache 2.0.
- **Airbyte / Singer** are cited below as *design references only* — their connector specs informed this design; none of their code is used.

Rule of thumb carried forward: use libraries through their documented APIs under permissive licenses; never paste external source code into the repo; check a dependency's license before adding it.

## Research notes and sources

Design pattern (Ports & Adapters / Hexagonal Architecture, and its YAGNI caution):
- [Hexagonal architecture — AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/hexagonal-architecture.html)
- [What is Hexagonal Architecture, aka "Ports and Adapters"](https://0x5.uk/2023/09/28/what-is-hexagonal-architecture/)

The universal "Document + Reader" connector pattern:
- [LlamaIndex — Data Connectors (LlamaHub)](https://developers.llamaindex.ai/python/framework/module_guides/loading/connector/)
- [LlamaHub connector registry](https://llamahub.ai/)

Delegating multi-format parsing to a library:
- [Unstructured — open-source ETL for documents (64+ file types), GitHub](https://github.com/Unstructured-IO/unstructured)
- [Unstructured — Using data connectors for multi-source ingestion](https://unstructured.io/insights/using-data-connectors-for-efficient-multi-source-ingestion)

Production RAG decision checklist (parsing, chunking, metadata, embeddings-as-migration, refresh/CDC, security, eval):
- [Unstructured — RAG systems best practices](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline)
- [Databricks — Build an unstructured data pipeline for RAG](https://docs.databricks.com/aws/en/generative-ai/tutorials/ai-cookbook/quality-data-pipeline-rag)

Connector-specification standards (design references):
- [Airbyte — Connector Specification Reference](https://docs.airbyte.com/platform/connector-development/connector-specification-reference)
- [Kestra — Why data integration will never be fully solved (Fivetran/Airbyte/Singer/dlt)](https://kestra.io/blogs/2023-10-11-why-ingestion-will-never-be-solved)

pgvector schema / metadata / namespace design:
- [Tiger Data — PostgreSQL as a vector database using pgvector](https://www.tigerdata.com/blog/postgresql-as-a-vector-database-using-pgvector)
- [Instaclustr — pgvector key features, pros and cons (2026)](https://www.instaclustr.com/education/vector-database/pgvector-key-features-tutorial-and-pros-and-cons-2026-guide/)
- [How we built a vector database for SEC filings using PostgreSQL + pgvector](https://dev.to/yashjoshi2109/how-we-built-a-vector-database-for-sec-filings-using-postgresql-pgvector-21n)
