# Offline unit tests for the 10-K section splitter — pure text in, no network or bs4 needed.
from genai.extraction.sections import split_sections

# A long filler body, comfortably above the splitter's minimum-section length.
_BODY = "x" * 300


def test_splits_all_target_items():
    text = "\n".join([
        "Item 1. Business", _BODY,
        "Item 1A. Risk Factors", _BODY,
        "Item 7. Management's Discussion and Analysis", _BODY,
        "Item 7A. Quantitative and Qualitative Disclosures", _BODY,
        "Item 8. Financial Statements", _BODY,
    ])
    result = split_sections(text)
    assert set(result) == {
        "Item 1 - Business",
        "Item 1A - Risk Factors",
        "Item 7 - Management Discussion and Analysis",
        "Item 7A - Quantitative and Qualitative Disclosures About Market Risk",
    }


def test_ignores_table_of_contents_picking_longest_body():
    # The TOC lists every item with almost no following text; the real section has a long body.
    toc = "\n".join([
        "Item 1. Business 3",
        "Item 1A. Risk Factors 8",
        "Item 7. MD&A 25",
        "Item 7A. Market Risk 40",
    ])
    real_risk = "R" * 500
    text = toc + "\nItem 1A. Risk Factors\n" + real_risk
    result = split_sections(text)
    # Only the real Risk Factors section (long body) survives; short TOC lines are dropped.
    assert list(result) == ["Item 1A - Risk Factors"]
    assert real_risk in result["Item 1A - Risk Factors"]


def test_returns_full_when_no_item_headers():
    text = "A filing with no recognizable item headers at all. " * 20
    assert split_sections(text) == {"full": text}


def test_returns_full_when_all_sections_too_short():
    text = "Item 1. Business\nshort\nItem 1A. Risk\nalso short"
    assert split_sections(text) == {"full": text}


def test_empty_text_returns_full_shape():
    assert split_sections("") == {"full": ""}


def test_handles_non_breaking_spaces_and_case():
    # HTML &nbsp; becomes \xa0; headers may be uppercase — both must still match.
    text = "ITEM\xa01A.\xa0RISK FACTORS\n" + ("y" * 300)
    result = split_sections(text)
    assert "Item 1A - Risk Factors" in result
