"""Offline unit tests for the strategy-selectable chunker — no network, no model load."""
import pytest

from genai.retrieval.chunker import chunk

# Defaults: 500 target tokens, 50 overlap tokens, ~0.75 words/token => 375 / 38 words.
_TARGET_WORDS = round(500 * 0.75)
_OVERLAP_WORDS = round(50 * 0.75)


def _long_text(n_sentences: int) -> str:
    # Many short sentences => predictable word count, forces multiple recursive chunks.
    return " ".join(f"This is sentence number {i} of the filing." for i in range(n_sentences))


def test_whole_strategy_returns_single_chunk():
    assert chunk("A short weather summary.", strategy="whole") == ["A short weather summary."]


def test_whole_strategy_empty_returns_no_chunks():
    assert chunk("", strategy="whole") == []
    assert chunk("   \n  ", strategy="whole") == []


def test_recursive_short_text_is_one_chunk():
    out = chunk("Just one short paragraph well under the budget.", strategy="recursive")
    assert len(out) == 1


def test_recursive_long_text_splits_into_multiple_chunks():
    # ~8 words/sentence * 200 = ~1600 words => at least a few chunks of ~375 words.
    out = chunk(_long_text(200), strategy="recursive")
    assert len(out) >= 3
    # Each chunk stays near the target — never wildly over (allow one extra piece beyond the budget).
    for c in out:
        assert len(c.split()) <= _TARGET_WORDS + 30


def test_recursive_chunks_overlap_by_configured_tail():
    out = chunk(_long_text(200), strategy="recursive")
    # Each chunk begins with the trailing overlap words of the previous chunk.
    for prev, nxt in zip(out, out[1:]):
        assert nxt.split()[:_OVERLAP_WORDS] == prev.split()[-_OVERLAP_WORDS:]


def test_recursive_empty_returns_no_chunks():
    assert chunk("", strategy="recursive") == []


def test_unknown_strategy_raises_value_error():
    with pytest.raises(ValueError):
        chunk("text", strategy="nonsense")
