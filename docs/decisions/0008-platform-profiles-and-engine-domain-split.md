# ADR 0008: Deployment profiles (AWS, Azure, local) and an engine/domain split for reuse

- Status: Accepted
- Date: 2026-11-04

## Context
The live system runs on AWS (one EC2 instance running K3s). I want the same code to deploy to Azure as well, and to run on a laptop for testing wherever that's practical. I also want to lift the reusable parts into future projects without dragging this project's data sources along.

## Decision
**Profiles.** One setting, `PLATFORM_PROFILE=aws|azure|local`, picks a default backend for every component. Per-component settings can override it, so mixes work (for example, AWS storage with Azure search).

| Component | aws | azure | local |
|---|---|---|---|
| Lake storage (Delta tables) | S3 | ADLS Gen2 | local folder |
| Search index | pgvector + Postgres full-text on K3s | Azure AI Search | pgvector in Docker |
| Embedder | local model in the pod | Azure OpenAI | local model |
| LLM | Anthropic / OpenAI | Azure OpenAI | Anthropic / OpenAI, or a test stub |
| Data service | FastAPI pod on K3s | Azure Container Apps | uvicorn / Docker |
| Secrets | Kubernetes secrets | Key Vault + managed identity | `.env` file |

**Interfaces.** Every component sits behind a small interface (`LakeStore`, `Parser`, `Embedder`, `SearchIndex`, `LLMProvider`, `AgentRunner`), and a factory picks the adapter from config. No call site imports a concrete adapter. One shared contract-test suite runs against every adapter, so "switchable" is proven by tests rather than promised.

**Engine vs. domains.** Reusable parts live in `agent_data/engine/`. Source-specific parts live in `agent_data/domains/<source>/`. The rules:
- The engine never imports a domain, Airflow or Dash.
- The package installs with optional extras per backend (for example `agent-data[pgvector]`, `[azure]`), so another project installs only what it uses.
- A missing extra raises a clear "install agent-data[x]" error.
- An import-layering check in CI enforces these boundaries.

## Alternatives considered
- **Azure only.** It would mean leaving a working, low-cost AWS system behind.
- **A single hard-coded stack.** Simpler to start, but every later change of backend becomes a rewrite.
- **A general-purpose framework for every backend at once.** Over-built. I add an adapter only when a profile actually needs it.

## Consequences
- **Wins:** a backend change is a config change, and a new project reuses the engine and writes only a domain folder.
- **Trade-offs:** each cloud adapter needs live tests against real services, which cost a little to run. They're marked `live` and kept out of CI.
