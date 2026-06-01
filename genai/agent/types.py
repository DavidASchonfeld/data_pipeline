from __future__ import annotations

from dataclasses import dataclass, field

from genai.retrieval.types import Passage


@dataclass
class TraceStep:
    """One step in the agent's reasoning log — what tool ran on a given turn, the inputs it was
    called with, a short preview of what came back, and the tokens that turn cost. The EPIC 10 chat
    UI renders this list as a live "here's what I did" trace, so it must stay human-readable and
    JSON-friendly (no raw payloads). `tool` is "final_answer" for the terminal turn where the model
    stops calling tools and writes its answer."""

    turn: int
    tool: str
    tool_input: dict = field(default_factory=dict)
    result_summary: str = ""        # truncated preview of the tool result — never the full payload
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None        # set when the tool raised (e.g. a refused SQL query)


@dataclass
class AgentResult:
    """The single object answer() returns: the final text, the full step-by-step trace, the source
    passages that backed the answer (deduped by id so the UI can cite each one), and the estimated
    USD cost of the whole question. Everything the chat UI needs to show an answer AND prove how it
    got there lives here."""

    answer_text: str
    trace: list[TraceStep] = field(default_factory=list)
    sources: list[Passage] = field(default_factory=list)
    cost_estimate: float = 0.0      # USD, summed across every LLM turn this question took
