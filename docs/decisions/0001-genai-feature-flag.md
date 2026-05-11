# ADR 0001: GenAI Feature-Flag Pattern

- Status: Accepted
- Date: 2026-05-10

## Context

I wanted to add an AI layer (text extraction, summaries, chat) on top of the existing data pipeline without touching anything that already works. The pipeline runs in production on a single small server. Any change that breaks the existing weather, stocks, or anomaly-detection flow would be a serious problem.

The challenge: AI features require extra packages, API keys, and K8s resources (a pgvector database). If those things are missing or misconfigured, the pipeline must still start and run normally. It cannot crash because an API key is absent or a package is not installed.

## Decision

I added a single environment variable, `GENAI_ENABLED`, that controls the entire AI layer. When it is set to `false` (the default), the AI layer does not exist as far as the running system is concerned:

- No extra Python packages are installed.
- No API keys are required.
- No new Kubernetes resources are deployed.
- No AI-related code runs during any DAG execution.
- The dashboard looks and behaves exactly as it did before.

When `GENAI_ENABLED=true`, the deploy script installs the extra packages, applies the API-key secret to the cluster, and activates the AI DAGs and dashboard tab.

Think of it like wiring in a new room of a house but leaving the circuit breaker off. All the wiring is there, tested, and ready — but no electricity flows until the switch is flipped.

## Alternatives considered

**No flag — always active.** Simpler code, but it means the pipeline would fail to start if an API key was missing or a package was not installed. Not acceptable for a production system.

**Separate git branch for the AI layer.** Common in team projects. Overkill here — maintaining two branches creates merge conflicts and makes it harder to see the whole project at once.

**Environment-specific deploy scripts.** A separate `deploy-genai.sh` script. Fragile: two scripts that need to stay in sync, and easy to forget which one was used last.

## Consequences

**Wins:**
- The pipeline is always safe to deploy with `GENAI_ENABLED=false`, even if the AI layer is half-finished.
- Removing the AI layer entirely takes three steps: flip the flag, redeploy, delete the AI source folders.
- Porting the AI layer to another project is straightforward because all AI code lives in self-contained folders.

**Trade-offs:**
- Every new AI-related deploy step must check the flag. It is easy to forget this when adding code quickly.
- The flag is a deploy-time setting, not a runtime toggle — changing it requires a redeploy, not just a config update.
