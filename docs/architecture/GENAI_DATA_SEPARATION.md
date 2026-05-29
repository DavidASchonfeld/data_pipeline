# GenAI Data Separation: Subjects, Sources, and Projects

> Plain-English answer to a question that keeps coming up: *"My pipeline covers two unrelated topics — stocks and weather. Do I need to keep them separate? Two tables? Two chatbots? What if someone asks about both? And how does any of this affect copying the tool to a totally different project later (say, frogs)?"*
>
> This page is the single source of truth for how data is kept separate (and kept together) in the GenAI layer. It builds on [ADR 0005](../decisions/0005-source-agnostic-ingestion.md) (the source-agnostic `chunks` table) and the GenAI roadmap.

---

## The one idea: three levels of separation, three mechanisms

The trap is treating "keep separate" as a single thing. There are **three** levels, each with its own mechanism and its own *strength* of separation. Using the wrong one either leaks data or breaks features.

| Level | What it is | Mechanism | Separation strength |
|---|---|---|---|
| **Deployment** | A whole installation of the tool | A **database** (its own pgvector + Snowflake) | **Hard** — nothing is shared |
| **Subject** | A topic inside one deployment | A **`namespace`** value in the shared `chunks` table | **Soft** — the one chatbot composes across subjects |
| **Source** | A feed of data for one subject | A **`metadata.source`** field + a reader adapter in `genai/sources/` | **Softest** — blended by default, filterable on demand |

The same primitive recurs at each scale: a `namespace` is the unit of "a subject," a database is the unit of "a deployment," and a `metadata.source` tag is the unit of "a feed." Once you see the three levels, every separation question answers itself.

---

## Level 1 — Subjects within one project (stocks vs weather)

**Do they need separate tables?** No. ADR 0005 deliberately replaced the old two-table design (`filing_chunks`, `weather_summaries`) with **one generic `chunks(namespace, external_id, text, embedding, metadata)` table**. Stocks live under one `namespace`, weather under another. Splitting them back into two tables would revert that decision and force new DDL for every future subject — the exact anti-pattern the ADR avoids.

> ⚠️ Do not confuse this with the **structured** Snowflake fact tables `FCT_FILING_EXTRACTS` and `FCT_WEATHER_SUMMARIES`. Those *are* correctly separate tables, because their columns differ. That is the structured-data layer and is unrelated to the RAG `chunks` table. Rule of thumb: different-shaped structured facts → different tables; meaning-searchable text → one `chunks` table separated by namespace.

**Do they need two chatbots?** No. There is **one** agent (the EPIC 9 orchestrator) and it acts as a **router**. It owns one tool per subject (`retrieve_filing_context`, `get_weather_summary`, `query_snowflake_marts`, `run_anomaly_check`) and decides which to call based on the question.

**What if someone asks about both?** It works — and it is a feature, not a contamination risk. A cross-subject question (*"Did the bad weather in Cupertino track Apple's revenue dip?"*) makes the agent fire **multiple** tool calls — one into the stocks namespace, one into weather — then synthesize a single answer. The chat UI shows sources from every namespace it touched. Precision is preserved at the **retrieval** layer (each tool scopes to its namespace via the metadata filter); integration happens at the **agent** layer (one orchestrator composes the pieces). Hard walls between stocks and weather would *break* this, which is why subject separation is intentionally soft.

---

## Level 2 — Multiple sources for one subject (e.g. SEC EDGAR *and* Yahoo for stocks)

"Source" and "subject" are different things. If one subject draws on several feeds — SEC EDGAR plus Yahoo Finance for stocks, or Open-Meteo plus NOAA for weather — the default is:

**Same namespace, distinguished by a `source` key in `metadata`. Not a new namespace, not a new table.**

Why this is the right default:

- The `chunks.metadata` JSONB column already has a GIN index built for exactly this kind of tagging (ADR 0005 / EPIC 2).
- The EPIC 8 retriever already accepts `filters` that map to `metadata->>'key'`, so `{"source": "sec_edgar"}` is a free scoping option — you can blend all sources *or* narrow to one with no new code.
- The common question — *"What are Apple's risk factors?"* — should draw on **all** stock feeds at once. Subject-level retrieval that blends sources is the normal case; per-source scoping is the occasional exception. Metadata gives you both. A separate namespace per source would force you to manually union them on every query.
- Adding a source is then exactly what `genai/sources/` exists for: a thin new `DataSource` adapter whose `iter_documents()` stamps `metadata.source` and writes into the *existing* namespace. No DDL, no new namespace, usually not even a new agent tool.

**Two rules when a subject has multiple sources:**

1. **Source-qualify the `external_id`** (e.g. `sec:AAPL:2023:item1a` vs `yahoo:AAPL:2023:summary`) so the `UNIQUE(namespace, external_id)` upsert de-duplicates per source instead of colliding.
2. **Use the same embedder for every source in a namespace.** Sources within one namespace share a single vector space, so they must share an embedder and dimension (currently `all-MiniLM-L6-v2`, dim 384).

**When does a source deserve its own namespace instead?** Only when you want *hard* separation — different trust levels, access control, or feeds that must never be blended in one answer (e.g. "official filings" vs "internet rumor scrape"). Otherwise, metadata is the lighter, correct tool.

---

## Level 3 — Separate projects (this pipeline vs a future "frog" project)

This is the only level with a **hard** wall, and the wall is the **database**, not the code.

**Key fact: the code carries no memory.** Copying the `genai/` folder copies zero stocks/weather data — there are no embeddings or filing text baked into Python files. All "memory" lives in external stores: the pgvector `chunks` table, the Snowflake tables, and the BM25 index that is rebuilt *from* `chunks`. So a copied tool only inherits the original project's data if you point it at the original project's databases.

- **Recommended / default:** give the new project its **own** pgvector pod and its **own** Snowflake database (the porting checklist's "adjust `config.py`" step). The copy starts empty and remembers nothing about stocks or weather. Contamination is impossible because there is no shared store.
- **If you ever deliberately share one database between projects** (not recommended), `namespace` is the only thing keeping them apart, and one retrieval path — the BM25 keyword index — must be scoped by namespace or it will blend everything (see the note below). Fresh databases avoid this entirely.
- Either way, the **per-domain adapters** still need rewriting per project (`agent/tools.py`, `eval/gold_set.json`, and a reader in `genai/sources/`). Those are domain-specific *code*, not shared memory, so they cannot leak data between projects. See the roadmap's "Reuse model: engine vs. adapters."

### The BM25 caveat (applies at every level)

Vector search filters by namespace/metadata, but a naive BM25 keyword index pulls **all** rows. If BM25 is not also scoped by namespace, the keyword half of hybrid search will blend subjects (and, in a shared database, projects). The retriever must apply the **same** filters — including `namespace` — to *both* the vector query and the BM25 candidate set before fusing them. This is specified in the roadmap's EPIC 8.

---

## Quick decision guide

- **New topic in this project** (e.g. news articles) → new `namespace` + an adapter + its tools. Same database, same chatbot.
- **New feed for an existing topic** (e.g. a second stock source) → same namespace, tag `metadata.source`, add a reader adapter. No new namespace, no DDL.
- **A whole new project** (e.g. frogs) → fresh pgvector + Snowflake databases. Inside it, topics become namespaces and feeds become sources again.
- **Sensitive or multi-user data** → namespace/metadata filtering is *not* a security boundary on its own; see the porting caution in [GENAI_RAG_REFERENCES](../reference/GENAI_RAG_REFERENCES.md).

---

## Related

- [ADR 0005 — Source-Agnostic Ingestion](../decisions/0005-source-agnostic-ingestion.md) — the `chunks` table and reader-port decision this page builds on.
- [GenAI Overview](../GENAI_OVERVIEW.md) — plain-English overview of the whole AI layer.
- [GENAI_RAG_REFERENCES](../reference/GENAI_RAG_REFERENCES.md) — external best-practice sources behind the design choices here.
- `GENAI_ROADMAP.md` (kept just outside the repo) — the step-by-step engineering plan; EPIC 8 (retriever/namespace filter) and the "Separation model" section mirror this page.
