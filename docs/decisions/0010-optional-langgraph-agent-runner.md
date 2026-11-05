# ADR 0010: The agent loop sits behind an interface; LangGraph is an optional runner

- Status: Accepted
- Date: 2026-11-04

## Context
ADR 0002 rejected LangChain-style wrappers for **talking to LLM providers**, and that still holds: providers stay behind my own small `LLMProvider` interface. Agent **orchestration** (plan → call tools → check → answer) is a separate concern. A graph-based orchestrator is a widely used way to express that loop, with built-in state handling and step limits.

## Decision
- The agent loop sits behind an `AgentRunner` interface with two implementations, picked by `AGENT_RUNNER`:
  - `native`: my own small state machine, rewritten for readability, with a cap of 8 tool calls per question and an enforced daily budget.
  - `langgraph`: the same loop expressed as a LangGraph graph.
- Both runners call the same typed tools and the same `LLMProvider`. Tool behaviour, governance and audit logging don't depend on which runner is active.
- External agents can also reach the same tools through the MCP server.

## Alternatives considered
- **Native loop only.** Fewer dependencies, but there's nothing to compare it against.
- **LangGraph only.** It would make a third-party framework the only way to run the agent, which contradicts the plug-and-play goal.

## Consequences
- **Wins:** I can compare the two runners on the same evaluation set, and either one can be removed without touching tools or data services.
- **Trade-offs:** two runners to keep passing the same tests. LangGraph is an optional install extra, so projects that don't use it don't pay for it.
