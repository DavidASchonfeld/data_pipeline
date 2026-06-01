# Which Models Does This Project Use? (Plain-English Guide)

A companion to `GENAI_ROADMAP.md`. It answers four questions about the GenAI layer:
how many models the finished project uses, which cost money and which don't, why,
and how to decide which to use when.

---

## How many models? Three kinds — but only one is an "LLM" in the chat sense.

| # | Model / engine | Where it runs | Costs money? | Used in |
|---|----------------|---------------|--------------|---------|
| 1 | **Generative LLM** (Claude or OpenAI — your `LLM_MODEL`) | External API | **Yes** (per token) | EPIC 4 extraction, EPIC 5 summaries, EPIC 9 agent, EPIC 11 grading |
| 2 | **Embedding model** (`all-MiniLM-L6-v2`, sentence-transformers) | Locally on your EC2 server | **No** (free) | EPIC 6/7 ingest, EPIC 8 retrieval |
| 3 | **BM25** (keyword ranking) | Locally, in-memory | **No** (free) | EPIC 8 retrieval |

So strictly: **one true LLM** (the generative one), **one embedding neural net**, and
**one keyword algorithm** (BM25 isn't a "model" at all — it's just term-frequency math,
no neural network, no training).

There's a subtle 4th *role*, but it reuses model #1: **EPIC 11's Ragas grader** uses a
generative LLM as an automated judge to score faithfulness/relevancy. It's the same
*kind* of API model (you can even point it at a cheap one), so it costs money too.

---

## Which cost money, and why

**Paid — the generative LLM (#1):** You're renting someone else's giant model on their
hardware. They charge per token in and out. Every weather summary, every filing
extraction, every chat answer, and every Ragas grade is a metered API call. This is why
your config has cost guardrails (`GENAI_MAX_COST_PER_QUERY` $0.05, `GENAI_DAILY_BUDGET`
$2.00) and why EPIC 9 estimates token cost and aborts before an expensive question.

**Free — the embedding model (#2):** `all-MiniLM-L6-v2` is a small (~80 MB) open-source
model that *you download once and run on your own server*. No network call leaves the
box, so no per-use charge — you only pay for the EC2 compute you're already running.
That's exactly why EPIC 6 chose a *local* embedder: embedding happens on **every chunk of
every filing on every ingest**, which would be enormous volume. Doing that against a paid
API would be the single biggest cost in the project; doing it locally makes it
effectively free.

**Free — BM25 (#3):** Pure arithmetic over word counts. No model, no API, no GPU. Costs
nothing.

---

## How to decide which to use when

The split falls naturally along **"generate language" vs. "find relevant text":**

**Use the generative LLM (#1, paid) only when you need *new words written or reasoning
done*:**
- Turning data into prose (weather summary, filing extract).
- Answering a user's question, deciding which tools to call (the agent).
- Judging answer quality (Ragas).
- These are low-volume, high-value, once-per-event — so paying per call is fine. And you
  can dial the *tier* down: the verified weather run used `gpt-4o-mini` because a
  2-sentence blurb doesn't need a frontier model. Reserve the expensive model for the
  agent's reasoning.

**Use the embedding model (#2, free) when you need to *measure meaning similarity at
scale*:**
- Converting every filing chunk into a fingerprint at ingest time.
- Converting a user's question into a fingerprint to search with.
- High-volume, runs constantly → must be local/free.

**Use BM25 (#3, free) when you need *exact keyword/term matching*:**
- Catching literal terms the embedding model might gloss over (specific tickers, names,
  numbers).
- It runs alongside the embedder, not instead of it — EPIC 8 fuses both with reciprocal
  rank fusion, because keyword search and meaning search each catch what the other misses.

**The mental model:** embedding + BM25 are the cheap, high-volume *librarians* that find
the right pages; the generative LLM is the expensive, low-volume *writer* that reads only
those pages and composes the answer. You spend money only on the writing/reasoning step,
and you keep that step's input small (via the free retrieval layer) precisely so the paid
calls stay cheap.

---

## Why vectors at all? Why not just paste data into Claude?

For small, structured data (like the weather summaries in EPIC 5), you *don't* need
vectors — and EPIC 5 deliberately skips them: 7 daily rows per city fit trivially in a
prompt, so it sends them straight to the LLM.

Vectors only matter for the *filings* path (EPIC 7+). A single 10-K is ~45 MB / hundreds
of pages, across many companies and years. You can't paste "all my data" into a chat
window: it won't fit in the context window, you'd pay to re-send the entire corpus on
every question, and accuracy drops as you stuff in irrelevant text. Embeddings let you
store every chunk once, then fetch only the handful of passages relevant to a question
and send just those. It's "look up the three relevant pages" instead of "re-read every
book in the library."

**Decision rule:**
- Data small enough to fit in a prompt, and you know which rows you need → just send it
  (EPIC 5).
- Huge corpus where *which* part is relevant depends on the question → use
  vectors/retrieval (EPIC 7+).
