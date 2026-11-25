# ADR 0011: The data lake lives in cloud object storage (S3 now, Azure next), not in Snowflake

- Status: Accepted
- Date: 2026-11-24

## Context
The `edgar_documents` DAG lands each company's raw 10-K (exact HTML, about 4.5 MB each, 337 MB for the first 75) plus Bronze Delta tables. Later layers (Silver, Gold) build on top. I needed to decide where these files and tables live.

## Decision
The lake lives in **cloud object storage**. One setting, `LAKE_URI`, points at it, and the code is identical everywhere:

| Profile | Where the lake is | Status |
|---|---|---|
| `aws` (live) | Private S3 bucket `data-pipeline-lake-<account>-<region>`, created by `terraform/lake.tf` | Live since 2026-11-24 |
| `azure` | Azure Data Lake Storage Gen2 (`abfss://…`), created in `terraform/azure/` | Planned (P3-1 / P3-2), no code changes needed |
| `local` | Gitignored `./.lake/` folder | Working |

Snowflake keeps the structured numbers (MARTS, ANALYTICS). It doesn't store the documents.

## Why
- **Documents belong in file storage.** Raw filings are big HTML files kept byte-for-byte. Object storage is built for that; a warehouse is built for querying rows.
- **Cost.** A few hundred MB in S3 or ADLS costs cents a month. Every Snowflake load or read runs a warehouse that bills credits.
- **Not tied to one vendor.** The same Delta tables work on AWS, Azure, a laptop, and later Spark or Databricks, and Snowflake can read them in place if needed.
- **Proof of origin.** Each raw file keeps a SHA-256 fingerprint in its sidecar, so any search answer can be traced back to the exact bytes the SEC published.

## Alternatives considered
- **Store everything in Snowflake.** Higher running cost, awkward for large HTML values, and it ties the lake to one warehouse.
- **Azure only, right now.** It would mean moving off the working, low-cost AWS server first. The Azure profile is added alongside it instead (see ADR 0008).

## Consequences
- **Wins:** cheap, cloud-neutral storage, and switching clouds means changing `LAKE_URI` instead of the code.
- **Trade-offs:** one more piece of infrastructure (the bucket, with `prevent_destroy` on it), and pods need the metadata hop limit of 2 to reach the server's S3 credentials (explained in `docs/agent_data/EDGAR_DOCUMENTS_DAG.md`).
