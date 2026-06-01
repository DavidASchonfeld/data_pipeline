from __future__ import annotations

import re

# Splits source text into bite-sized chunks before embedding.
#
# The strategy is selectable per data source (ADR 0005): long 10-K sections use a recursive,
# boundary-aware split; short weather summaries are stored whole. Adding a new source picks an
# existing strategy or registers a new one here — the ingest engine never changes.
#
# Token-awareness WITHOUT a tokenizer dependency: a "token" is ~0.75 of an English word, so I
# approximate token budgets from word counts (tokens * 0.75 = words). This avoids adding tiktoken
# (a new dependency + license entry) for what is only an approximate length target anyway.

_WORDS_PER_TOKEN = 0.75

# Sentence boundary: end punctuation followed by whitespace. Good enough to avoid splitting mid-fact.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _word_count(text: str) -> int:
    # Whitespace-delimited word count — the unit the token budget is approximated in.
    return len(text.split())


def _atomic_pieces(text: str, max_words: int) -> list[str]:
    # Break text into pieces no larger than max_words, splitting at the most natural boundary first:
    # paragraphs, then sentences, then (last resort) fixed word windows. Keeps facts intact where possible.
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if _word_count(para) <= max_words:
            pieces.append(para)
            continue
        # Paragraph too big — drop to sentences.
        for sent in _SENTENCE_SPLIT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if _word_count(sent) <= max_words:
                pieces.append(sent)
                continue
            # Sentence still too big (rare) — hard-split into word windows.
            words = sent.split()
            for i in range(0, len(words), max_words):
                pieces.append(" ".join(words[i : i + max_words]))
    return pieces


def _overlap_tail(chunk_text: str, overlap_words: int) -> str:
    # The trailing overlap_words of a chunk, carried into the next chunk so a fact spanning the
    # boundary stays retrievable from both sides. Empty string when overlap is disabled.
    if overlap_words <= 0:
        return ""
    words = chunk_text.split()
    return " ".join(words[-overlap_words:])


def _strategy_recursive(text: str) -> list[str]:
    # Greedily merge atomic pieces into chunks near the target size, with a carried-over overlap tail.
    from genai.config import GENAI_CHUNK_OVERLAP_TOKENS, GENAI_CHUNK_TARGET_TOKENS

    if not text or not text.strip():
        return []

    target_words = max(1, round(GENAI_CHUNK_TARGET_TOKENS * _WORDS_PER_TOKEN))
    overlap_words = max(0, round(GENAI_CHUNK_OVERLAP_TOKENS * _WORDS_PER_TOKEN))

    pieces = _atomic_pieces(text, target_words)
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        # Flush the current chunk once adding this piece would push it past the target, then seed the
        # next chunk with the overlap tail of what was just flushed.
        if current and _word_count(current) + _word_count(piece) > target_words:
            chunks.append(current)
            current = _overlap_tail(current, overlap_words)
        current = f"{current} {piece}".strip()

    if current.strip():
        chunks.append(current.strip())
    return chunks


def _strategy_whole(text: str) -> list[str]:
    # No chunking — a short document (e.g. a 2–4 sentence weather summary) is already one chunk.
    return [text.strip()] if text and text.strip() else []


# Registry of named strategies — a new source picks one of these (or registers its own here).
_STRATEGIES = {
    "recursive": _strategy_recursive,
    "whole": _strategy_whole,
}


def chunk(text: str, strategy: str = "recursive") -> list[str]:
    """Split text into chunks using the named strategy. Raises ValueError for an unknown strategy."""
    fn = _STRATEGIES.get(strategy)
    if fn is None:
        raise ValueError(f"unknown chunk strategy {strategy!r}; supported: {sorted(_STRATEGIES)}")
    return fn(text)
