# genai.extraction — fetch and parse source documents (SEC EDGAR 10-K today; more sources later).
# Kept deliberately thin: fetch (HTTP), clean (HTML->text), and split (sections) are separate,
# reusable pieces so a future data source reuses the splitter/HTTP helper and only writes its own
# "how do I locate the document" step. See ADR 0005 for the source-agnostic design.
