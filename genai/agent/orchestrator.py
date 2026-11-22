# The orchestrator: the single public entry point of the agent layer. Given a plain-English question
# it runs a bounded tool-use loop — ask the LLM, run any tool it requests, feed the result back, and
# repeat until the model writes a final answer or the turn/cost limits stop it. Every step is recorded
# in a trace so the answer can always be explained and audited. It is provider-agnostic: it only ever
# calls provider.chat(...) and provider.build_tool_turn(...), so swapping Anthropic for OpenAI changes
# nothing here.

from __future__ import annotations

import inspect
import json
import logging

from genai.agent.cost import cost_of, estimate_tokens, project_call_cost
from genai.agent.errors import AgentError
from genai.agent.tools import TOOL_DISPATCH, TOOL_SPECS
from genai.agent.types import AgentResult, TraceStep
from genai.config import GENAI_AGENT_MAX_TURNS, GENAI_MAX_COST_PER_QUERY, LLM_MODEL
from genai.retrieval import Passage, RetrieverError

logger = logging.getLogger(__name__)

# Largest reply (in tokens) any single orchestration turn may produce. A tool-choosing turn or a final
# answer never needs the 2048 default, so capping lower trims output cost and tightens the cost projection.
_MAX_TOKENS_PER_TURN = 1024

# What the model is told it is and which tools it has. Kept short and concrete: ground company/filing
# questions in retrieved text, weather in the weather tool, and never write anything but a SELECT.
_SYSTEM_PROMPT = (
    "You are a financial-data analyst assistant for a stock and weather data pipeline. "
    "Answer the user's question using the tools provided, then give a clear, well-grounded answer.\n"
    "- Use retrieve_filing_context for anything about a company's risks, business, or strategy from its SEC filings.\n"
    "- Use get_weather_summary for weather in a city for a given week.\n"
    "- Use run_anomaly_check for flagged year-over-year financial anomalies.\n"
    "- Use query_snowflake_marts only for read-only SELECTs against MARTS/ANALYTICS tables, with %(name)s placeholders.\n"
    "Call as many tools as you need to cover every part of the question (including questions that span "
    "more than one subject), cite what the sources say, and never attempt to modify data."
)


def _stringify_output(output) -> str:
    # Turn any tool's return value into the text the model sees as the tool result.
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output and isinstance(output[0], Passage):
        # Passages: feed back the passage text plus a short source tag so the model can ground its answer.
        parts = []
        for p in output:
            tag = p.metadata.get("ticker") or p.metadata.get("city") or p.source
            parts.append(f"[{tag}] {p.text}")
        return "\n\n".join(parts) if parts else "No passages found."
    # Rows (list of dicts) or anything else — JSON is compact and unambiguous for the model. `default=str`
    # safely renders dates/Decimals the Snowflake driver returns.
    return json.dumps(output, default=str)


def _summarize_output(output) -> str:
    # A short, human-readable preview for the trace — never the full payload (the UI shows this verbatim).
    if isinstance(output, str):
        return output if len(output) <= 200 else output[:197] + "..."
    if isinstance(output, list):
        if output and isinstance(output[0], Passage):
            return f"{len(output)} passage(s)"
        return f"{len(output)} row(s)"
    return str(output)[:200]


def _estimate_input_tokens(messages: list[dict], system: str) -> int:
    # Rough size of the next request: the system prompt, the running conversation, and the tool specs
    # (which are re-sent every turn). Serialized to JSON so nested content blocks are counted too.
    blob = system + json.dumps(messages, default=str) + json.dumps(TOOL_SPECS)
    return estimate_tokens(blob)


def _check_tool_args(fn, tool_input) -> str | None:
    # Make sure the model's arguments actually fit the tool before calling it, so a hallucinated or
    # missing argument comes back to the model as a fixable error instead of crashing the question.
    if not isinstance(tool_input, dict):
        return f"tool input must be an object, got {type(tool_input).__name__}"
    try:
        inspect.signature(fn).bind(**tool_input)
    except TypeError as exc:
        return f"bad arguments: {exc}"
    return None


def answer(question: str) -> AgentResult:
    """Answer `question` by letting the LLM call the agent's tools (up to GENAI_AGENT_MAX_TURNS turns),
    enforcing the per-query cost cap, and returning the final text together with the full trace, the
    source passages, and the estimated cost. This is the agent layer's one public function."""
    from genai.llm import get_llm_provider
    from genai.llm.base import LLMProviderError

    provider = get_llm_provider()
    messages: list[dict] = [{"role": "user", "content": question}]
    result = AgentResult(answer_text="")
    accumulated_cost = 0.0

    for turn in range(1, GENAI_AGENT_MAX_TURNS + 1):
        # Cost gate BEFORE the call: if the worst-case cost of this turn would exceed the cap, stop now
        # and answer from what we already have rather than overspend.
        projected = project_call_cost(
            _estimate_input_tokens(messages, _SYSTEM_PROMPT), _MAX_TOKENS_PER_TURN, LLM_MODEL
        )
        if accumulated_cost + projected > GENAI_MAX_COST_PER_QUERY:
            logger.warning("cost cap reached before turn %d (would be $%.4f)", turn, accumulated_cost + projected)
            result.trace.append(TraceStep(
                turn=turn, tool="cost_cap_reached",
                result_summary=f"stopped before turn {turn}: projected ${accumulated_cost + projected:.4f} "
                               f"exceeds cap ${GENAI_MAX_COST_PER_QUERY:.4f}",
            ))
            if not result.answer_text:
                result.answer_text = (
                    "I reached the cost limit for a single question before I could finish. "
                    "Here is what I gathered so far; please try a narrower question."
                )
            break

        try:
            response = provider.chat(
                messages=messages,
                tools=TOOL_SPECS,
                system=_SYSTEM_PROMPT,
                max_tokens=_MAX_TOKENS_PER_TURN,
            )
        except LLMProviderError as exc:
            # The provider already wrapped the SDK error; re-wrap as the agent layer's type with context.
            raise AgentError(f"LLM call failed on turn {turn}: {exc}") from exc

        # Bill the ACTUAL usage the provider reported.
        usage = response.get("usage", {})
        accumulated_cost += cost_of(usage, response.get("model", LLM_MODEL))
        tool_calls = response.get("tool_calls") or []

        # No tool calls → the model is done; record the final answer and stop.
        if not tool_calls:
            result.answer_text = response.get("content", "")
            result.trace.append(TraceStep(
                turn=turn, tool="final_answer", result_summary=_summarize_output(result.answer_text),
                input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            ))
            break

        # Run each requested tool. Collect outputs keyed by call id for the result turn, record a trace
        # step per call, and gather any retrieved passages as sources. A tool error is recorded and fed
        # back to the model (so it can recover) — never swallowed, never fatal to the whole question.
        tool_outputs: dict[str, str] = {}
        for index, call in enumerate(tool_calls):
            name, call_id, tool_input = call["name"], call["id"], call.get("input", {})
            fn = TOOL_DISPATCH.get(name)
            # One LLM call can request several tools, but its tokens were spent once — so I put the turn's
            # usage on the first step only; otherwise summing the trace would double-count the bill.
            first = index == 0
            step = TraceStep(
                turn=turn, tool=name, tool_input=tool_input if isinstance(tool_input, dict) else {},
                input_tokens=usage.get("input_tokens", 0) if first else 0,
                output_tokens=usage.get("output_tokens", 0) if first else 0,
            )
            arg_problem = _check_tool_args(fn, tool_input) if fn is not None else None
            if fn is None:
                step.error = f"unknown tool '{name}'"
                tool_outputs[call_id] = f"Error: unknown tool '{name}'."
            elif arg_problem:
                # Fed back so the model can retry with the right arguments.
                step.error = arg_problem
                tool_outputs[call_id] = f"Error: {arg_problem}."
                logger.info("tool %s called with bad arguments: %s", name, arg_problem)
            else:
                try:
                    output = fn(**tool_input)
                    tool_outputs[call_id] = _stringify_output(output)
                    step.result_summary = _summarize_output(output)
                    # Keep passage sources so the answer can be cited; deduped after the loop.
                    if isinstance(output, list) and output and isinstance(output[0], Passage):
                        result.sources.extend(output)
                except (AgentError, RetrieverError) as exc:
                    # Includes SqlSafetyError — e.g. a refused "drop the financials table" — and a failed
                    # filing search, so a pgvector/BM25 outage is reported to the model, not fatal.
                    step.error = str(exc)
                    tool_outputs[call_id] = f"Error: {exc}"
                    logger.info("tool %s refused/failed: %s", name, exc)
            result.trace.append(step)

        # Thread the assistant tool-use turn and the tool results back into the conversation, in the
        # provider's own wire format, and loop.
        messages.extend(provider.build_tool_turn(response, tool_outputs))
    else:
        # Loop ran the full turn budget without a final answer — give the best text we have.
        if not result.answer_text:
            result.answer_text = (
                "I used all my available steps without reaching a final answer. "
                "Please try asking a more specific question."
            )

    # Dedupe sources by passage id (Passage is an unfrozen dataclass, so dedupe by hand) and finalize cost.
    seen: set[int] = set()
    deduped: list[Passage] = []
    for p in result.sources:
        if p.id not in seen:
            seen.add(p.id)
            deduped.append(p)
    result.sources = deduped
    result.cost_estimate = accumulated_cost
    return result
