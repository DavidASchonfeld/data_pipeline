# ADR 0007: Agents get numbers from an approved metric catalog, not free-form SQL

- Status: Accepted
- Date: 2026-11-04

## Context
The first agent had a tool that let the AI write its own SQL against Snowflake, guarded by a parser that only allows a single read-only `SELECT` on approved schemas. That guard works, and its tests prove it blocks stacked statements and writes. But free-form SQL is still hard to govern:
- A valid `SELECT` can be slow or expensive.
- Its answers are hard to audit ("which definition of revenue did it use?").
- Its behaviour is hard to test, because the space of possible queries is unbounded.

## Decision
I replace free-form SQL with a **governed metric catalog**:
- `catalog.yaml` lists each approved metric with its name, plain-English definition, unit, and the dbt view that backs it.
- **Two tools:** `describe_metrics` lets the agent discover what it can ask for. `query_financials(ticker, metric, fiscal_years)` builds a **parameterized** query from the catalog only.
- **Defense in depth:** queries run as a read-only Snowflake role with a statement timeout and a row cap.
- **Backing model:** a long-format dbt model, `fct_financial_metrics` (ticker, fiscal year, metric, value, unit, source filing), backs the catalog.
- **Test reuse:** I keep the existing SQL-safety tests as an adversarial suite, to show that crafted inputs can't escape the parameterized path.

## Alternatives considered
- **Keep text-to-SQL with the parser allowlist.** More flexible, but it would also need cost limits, a read-only role and ongoing allowlist upkeep, and answers would still be harder to audit.
- **Offer both, behind a flag.** More code paths to secure and test, for a flexibility the agent doesn't need: the questions it answers map to a small set of metrics.

## Consequences
- **Wins:** every number the agent cites has one documented definition and a source filing, every query is testable, and cost is bounded.
- **Trade-offs:** a new metric needs a catalog entry and possibly a dbt change before the agent can use it. That's deliberate: the catalog is the governance step.
