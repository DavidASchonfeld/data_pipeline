# agent: the tool-using orchestrator (EPIC 9). Import the public API from here, never from the
# concrete modules, so callers (the EPIC 10 chat UI) depend on one stable entry point. answer() takes
# a question and returns an AgentResult carrying the final text, a step-by-step trace, the source
# passages, and the estimated cost.
from genai.agent.errors import AgentError
from genai.agent.orchestrator import answer
from genai.agent.types import AgentResult, TraceStep

__all__ = ["answer", "AgentResult", "TraceStep", "AgentError"]
