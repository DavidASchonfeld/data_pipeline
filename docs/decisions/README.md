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
