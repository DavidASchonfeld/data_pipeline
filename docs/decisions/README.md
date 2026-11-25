# Architecture Decision Records

This folder records the significant design choices made while building this project — one short document per decision.

## What is an ADR?

An Architecture Decision Record (ADR) is a short document that captures:
- **The problem** being solved
- **The choice** that was made
- **The alternatives** that were considered
- **The trade-offs** of the chosen approach

Think of it like a journal entry for each major design fork in the road. It answers the question "why did I build it this way?" so the answer is still there six months later.

## How to read them

Each ADR has a number (0001, 0002, …) and a title. Open any one of them — they are written to be understood without technical background. Most are one to two pages long.

## Index

| # | Title | Topic |
|---|---|---|
| [0001](0001-genai-feature-flag.md) | GenAI feature-flag pattern | How the entire AI layer is turned on or off with a single setting |
| [0002](0002-llm-provider-abstraction.md) | LLM provider abstraction | How the project avoids being locked into any single AI service |
| [0003](0003-secret-handling-pattern.md) | Secret-handling pattern | How API keys and passwords are kept out of the codebase |
| [0004](0004-prompt-caching-provider-internal.md) | Prompt caching is provider-internal | Why caching is handled inside each provider, not as a shared setting |
| [0005](0005-source-agnostic-ingestion.md) | Source-agnostic ingestion | How the layer is made to ingest "anything" later — one reader interface + a generic schema, without rewriting the engine |
| [0006](0006-rewrite-alongside-then-cut-over.md) | Rebuild alongside, then cut over | Why the AI layer is rebuilt as a new package next to the old one, with a measured switch-over |
| [0007](0007-governed-metric-catalog.md) | Governed metric catalog | Why agents get numbers from approved metrics instead of writing their own SQL |
| [0008](0008-platform-profiles-and-engine-domain-split.md) | Deployment profiles + engine/domain split | How the same code runs on AWS, Azure or a laptop, and how parts are reused in other projects |
| [0009](0009-delta-lake-via-delta-rs.md) | Delta Lake via delta-rs | Where the raw → cleaned → search-ready filing layers are stored, and why without Spark |
| [0010](0010-optional-langgraph-agent-runner.md) | Optional LangGraph agent runner | Why the agent loop sits behind an interface with a native and a LangGraph version |
| [0011](0011-lake-in-object-storage-not-snowflake.md) | Lake in object storage, not Snowflake | Where raw filings and lake tables live (S3 now, Azure ADLS next, a folder locally) and why not Snowflake |

## How to add a new ADR

1. Copy the template below into a new file: `NNNN-short-title.md` (use the next number in sequence).
2. Fill in each section in plain language.
3. Add a row to the index table above.

```markdown
# ADR NNNN: <decision title>

- Status: Accepted
- Date: YYYY-MM-DD

## Context
What problem is being solved? What constraints apply?

## Decision
The choice that was made, in plain language.

## Alternatives considered
What else was on the table, and why was it not chosen?

## Consequences
What does this decision mean going forward — both the wins and the trade-offs?
```
