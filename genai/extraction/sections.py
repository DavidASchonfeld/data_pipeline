from __future__ import annotations

import logging
import re

# Splits a 10-K's plain text into the named sections the GenAI layer extracts from.
#
# A 10-K is organised into numbered "Items". I pull the four that carry the qualitative content
# worth sending to an LLM. The signature stays generic (text -> {section: text}) so a future data
# source can register its own splitter strategy without touching the fetch/clean steps (ADR 0005).

logger = logging.getLogger(__name__)

# The SEC item codes I extract, mapped to the friendly section names returned to callers.
_TARGET_ITEMS: dict[str, str] = {
    "1": "Item 1 - Business",
    "1A": "Item 1A - Risk Factors",
    "7": "Item 7 - Management Discussion and Analysis",
    "7A": "Item 7A - Quantitative and Qualitative Disclosures About Market Risk",
}

# Matches an item header at the start of a line, e.g. "Item 1.", "ITEM 1A:", "Item 7 —".
# Anchoring at line start (MULTILINE) avoids matching "item 7" inside ordinary prose. The code
# group captures the number plus optional letter suffix (1, 1A, 7A). \s matches non-breaking
# spaces too (Python's re treats \xa0 as whitespace), so HTML &nbsp; gaps are handled.
_ITEM_HEADER = re.compile(
    r"^\s*item\s+(\d{1,2}[a-z]?)\s*[\.\:\-–—]",
    re.IGNORECASE | re.MULTILINE,
)

# A real section body is at least this long; shorter matches are table-of-contents lines, not content.
_MIN_SECTION_CHARS = 200


def split_sections(text: str) -> dict[str, str]:
    """Split 10-K text into {section_name: text} for Items 1, 1A, 7, 7A.

    Conservative by design: any item that can't be cleanly identified is dropped, and if none of
    the target items are found the whole filing is returned under the key "full" so callers always
    get usable content rather than an empty result.
    """
    if not text or not text.strip():
        # Nothing to split — hand back an empty "full" so the caller still gets the expected shape.
        return {"full": text or ""}

    # Every item header in document order; I use these positions to slice each section's body.
    headers = [(m.group(1).upper(), m.start(), m.end()) for m in _ITEM_HEADER.finditer(text)]
    if not headers:
        logger.info("No 10-K item headers found; returning whole filing under 'full' (%d chars)", len(text))
        return {"full": text}

    # All header start positions, sorted, so I can find where any given section ends (next header).
    starts = sorted(start for _, start, _ in headers)

    sections: dict[str, str] = {}
    for code, name in _TARGET_ITEMS.items():
        body = _best_body_for(code, headers, starts, text)
        if body is not None:
            sections[name] = body

    if not sections:
        logger.info("Item headers present but no target section cleanly extracted; returning 'full'")
        return {"full": text}

    logger.info("Split 10-K into %d sections: %s", len(sections), ", ".join(sections))
    return sections


def _best_body_for(
    code: str,
    headers: list[tuple[str, int, int]],
    starts: list[int],
    text: str,
) -> str | None:
    """Return the longest plausible body for one item code, or None if none is long enough.

    A 10-K lists every item twice — once in the table of contents, once as the real section. The
    TOC entry has almost no text before the next header, so picking the occurrence with the longest
    body reliably selects the real section over the TOC line.
    """
    best: str | None = None
    for header_code, _, header_end in headers:
        if header_code != code:
            continue
        # The section ends at the next header anywhere after this one.
        next_start = next((s for s in starts if s > header_end), len(text))
        body = text[header_end:next_start].strip()
        if len(body) >= _MIN_SECTION_CHARS and (best is None or len(body) > len(best)):
            best = body
    return best
