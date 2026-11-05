# ADR 0009: Layered filing storage in Delta Lake, written with delta-rs

- Status: Accepted
- Date: 2026-11-04

## Context
Filings need a layered home (raw → cleaned → search-ready) so every search result can be traced back to the exact filing and content hash it came from, and so any layer can be rebuilt from the one below. The live server has tight memory limits: the scheduler pod is capped at 3 GiB, so running Spark there isn't practical.

## Decision
I store the layers as **Delta Lake** tables:
- **Bronze:** the ingestion log and raw documents.
- **Silver:** filings and parsed elements.
- **Gold:** chunks, embeddings and the index-sync log.
- **Ops:** the audit log.

I read and write them with **delta-rs** (the `deltalake` Python package). It's Rust-based, light on memory, and needs no Spark. The same code writes to a local folder, `s3://` or `abfss://`, and only the table URI changes per profile. A Spark or Databricks job can read and write the same tables later without a migration.

## Alternatives considered
- **Keep the layers in Snowflake tables.** That works, but it ties storage to one warehouse and to its compute cost, and it's harder to reuse in other projects.
- **Plain Parquet files.** Lighter still, but no ACID writes, no time travel and no change feed, which the index sync needs.
- **Spark everywhere.** Too heavy for the current server.

## Consequences
- **Wins:** one table format across AWS, Azure and local, cheap incremental updates through the change feed, and a clean path to Spark later.
- **Trade-offs:** delta-rs supports fewer table features than Spark-based Delta. I avoid features it can't write.
