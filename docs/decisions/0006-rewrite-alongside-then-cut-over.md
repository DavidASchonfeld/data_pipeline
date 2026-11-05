# ADR 0006: Rebuild the AI layer as a new package next to the old one, then cut over

- Status: Accepted
- Date: 2026-11-04

## Context
The first version of the AI layer (`genai/`) works end to end, but it grew one stage at a time and is now harder to read than it should be:
- One function in the tool loop is over 100 lines long.
- The Snowflake connection code is copied in four places.
- Some modules are dead code.
- There are two real bugs: a search failure surfaces as an unhandled server error, and token counts are doubled when the AI calls several tools at once.

I also want to grow the layer into a governed data product: layered storage, a typed data service, access tiers, audit logging, and measured search quality. Deployment should be switchable between AWS and Azure, and parts should be reusable in other projects.

## Decision
I build a new package, `agent_data/`, **next to** `genai/` instead of editing `genai/` in place.
- **Reusable `engine/`:** interfaces, adapters, chunking, search fusion, the service skeleton, the agent loop and the scorers. The engine never imports project-specific code.
- **`domains/` folder:** one folder per data source (`edgar/`, `weather/`) holding that source's readers, tools, metric catalog and test questions.
- **What I carry over:** the proven pieces (rate-limited EDGAR client, section splitter, hash-skip loading, orphan deletes, LLM providers) are ported and cleaned up, not redesigned.
- **Cut-over gate:** I switch the chat tab and the AI service to the new package only after a labelled question set shows the new search scores at least as well as the old one. After one green week, I delete the old retrieval, agent and service code.

## Alternatives considered
- **Refactor `genai/` in place, one module at a time.** Lower risk, but the old layout (no engine/domain split, no layered storage) would shape the new design. Untangling it in place would take longer than rebuilding cleanly.
- **Start a brand-new repository.** A clean slate, but I would maintain two systems and redo deploy, CI and attribution work that already exists here.

## Consequences
- **Wins:** a clearer layout where each folder is one stage, an engine I can install in other projects, and a measured parity gate so the rebuild can't quietly get worse.
- **Trade-offs:** for a while there are two AI packages in the repo. The old one stays live until cut-over, so the public dashboard never breaks mid-rebuild.
