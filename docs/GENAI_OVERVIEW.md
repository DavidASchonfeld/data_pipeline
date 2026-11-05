# GenAI Layer — Plain-English Overview

> This page is written for **anyone**, technical or not. It answers two questions:
> 1. **The big picture** — what the AI features are meant to do for this project (not a step-by-step plan).
> 2. **What's actually built so far** — and where each piece lives.
>
> For the reasoning behind the design, see the decision records in `docs/decisions/`. For install/uninstall instructions, see `genai/README.md`.

---

## In one sentence

This project already runs a data pipeline that automatically collects company financial filings and weather data and shows them on a dashboard. The **GenAI layer** adds an artificial-intelligence assistant on top of that data, so a person can ask plain-English questions and get plain-English answers backed by the project's own data.

---

## The master plan — what the AI *will* do

Think of the finished AI layer as **four abilities**, all sharing one safety rule at the bottom.

### 1. Read dense documents and pull out the important facts
Every public company files a long annual report (a "10-K") with the U.S. government. These run hundreds of pages. The AI will read each report and pull out the parts that matter — the company's biggest risks and what it says about future revenue — and save them as tidy, searchable rows in the database. *A human no longer has to read the whole filing to find the key points.*

### 2. Write short, readable weather summaries
The pipeline already collects detailed hourly weather for several cities. The AI will turn each week's raw numbers into a short paragraph anyone can read — for example, *"This week in Chicago was unusually warm, with a sharp cold front on Thursday."* — and save one summary per city per week.

### 3. Answer questions in a chat box on the dashboard
A new **"Chat" tab** on the website will let anyone type a question like *"What were Apple's biggest risks last year?"* The screen shows two panels side by side:
- **Left:** the actual source passages the system found in your own data.
- **Right:** the AI's plain-English explanation, written *only* from those sources.

This is important: the AI does not make things up from general knowledge — it answers from the project's real data, and it shows its work. (The technical name for this approach is "retrieval-augmented generation," or RAG.)

### 4. Grade its own answers
To make sure the AI stays trustworthy, there's a built-in "exam." A fixed list of test questions (with known correct answers) is run through the assistant regularly, and each answer is automatically scored for accuracy and relevance. Scores are tracked over time so any drop in quality is caught early.

### The safety rule underneath everything
- **One master on/off switch.** A single setting (`GENAI_ENABLED`) turns the entire AI layer on or off. When it's off — which is the default — the existing pipeline and dashboard behave **exactly** as they did before, with zero AI activity and zero extra cost.
- **Spending limits.** There are hard caps on how much a single question can cost and how much can be spent per day, so the AI bill can never run away.
- **Swappable AI brain.** The project isn't locked into one AI company. Today it uses Anthropic; switching to OpenAI is a one-line setting change.
- **Runs on the same single server.** No new machine is added. The AI work is carefully sandboxed so it never crashes the existing pipeline.

---

## How the pieces fit together (simple diagram)

```
   Company filings (10-K)            Weather data
            │                             │
            ▼                             ▼
   [AI reads & extracts]        [AI writes summaries]
            │                             │
            └──────────────┬──────────────┘
                           ▼
            A searchable "meaning" database
              (finds text by meaning, not
               just exact keyword matches)
                           │
                           ▼
        Chat tab on the dashboard website
     ┌─────────────────────┬─────────────────────┐
     │  Sources from your  │  AI's plain-English  │
     │     own data        │   explanation        │
     └─────────────────────┴─────────────────────┘

        A separate "exam" grades the answers over time.
```

---

## What's actually built so far

The first build was organized into 11 numbered stages. Here's the honest status (updated 2026-11-04).

| Stage | What it delivers | Status |
|------|------------------|--------|
| **1. Foundation + AI connection** | The on/off switch, the settings file, and a working connection to the AI (Anthropic, with OpenAI ready as a backup) | ✅ Done |
| **2. The "meaning" database + AI toolbox** | The pgvector search database and the AI software libraries the server needs | ✅ Done |
| **3. Filing reader** | Downloads a company's full annual report and splits it into named sections | ✅ Done |
| **4. Filing fact-extractor** | Sends those sections to the AI and saves the key facts to Snowflake | ✅ Done |
| **5. Weather summaries** | The optional weekly weather paragraph per city | ✅ Done |
| **6. "Meaning translator" (embedder)** | Translates any text into a list of numbers that captures its meaning, so passages that mean similar things sit close together | ✅ Done |
| **7. Loader** | Loads filing text and weather summaries into the search database, skipping anything unchanged | ✅ Done |
| **8. Search engine** | Finds the most relevant passages for a question (keyword + meaning combined) | ✅ Done |
| **9. The decision-maker** | The AI "brain" that picks which tools to use and writes the final answer | ✅ Done |
| **10. Chat tab** | The two-panel chat screen on the website, backed by a small AI service running in its own pod | ✅ Built — live check pending |
| **11. The exam** | Automatic answer-grading and score tracking | ⬜ Moved into the next phase (below) |

**In short:** the whole question-answering loop works end to end. The "exam" hasn't been built yet.

### What comes next

The next phase rebuilds this layer as a cleaner, reusable package (`agent_data/`) and grows it into a governed data product:
- **Layered storage for filings:** raw → cleaned → search-ready, with every passage traceable back to its exact SEC filing.
- **A governed data service:** a small API, plus an MCP server so AI agents can call it as tools. Agents can only use approved financial metrics, never free-form SQL.
- **Access tiers and audit logging:** a record of who asked what.
- **The exam:** a labelled set of test questions that measures search quality with real numbers.
- **Deployment profiles:** the same code runs on AWS (today's system), on Azure, or on a laptop for testing.

The design choices are recorded in `docs/decisions/0006`–`0010`.

---

## Where each finished piece lives

Everything is kept in self-contained folders so the whole AI layer can be removed by deleting them and flipping the switch off.

| Piece | Where it lives | What it is, in plain terms |
|------|----------------|----------------------------|
| The on/off switch & all settings | `genai/config.py` | One file that holds the master switch, which AI to use, and the spending caps |
| The AI connection | `genai/llm/` | The code that talks to the AI service; `_factory.py` picks Anthropic or OpenAI based on the setting |
| Filing reader + section splitter | `genai/extraction/` | Downloads 10-K filings politely (rate-limited) and splits out the risk, business and management-discussion sections |
| The "meaning translator" | `genai/embedding/` | Turns text into meaning-numbers using a small free model that runs on the server |
| Loader + search engine | `genai/retrieval/` | Splits text into passages, loads them, and searches by keyword and by meaning, then blends the two rankings |
| The decision-maker | `genai/agent/` | The tool-calling loop, its tools, the SQL safety check, and the cost cap |
| Background jobs | `genai/runners/`, `airflow/dags/genai_dags/` | The scheduled jobs that extract facts and load the search database, run in a separate Python environment so they can't starve the scheduler of memory |
| The AI service | `genai/service/app.py`, `infra/genai/genai-api/` | A small web service, in its own pod, that the chat tab calls |
| Chat tab | `dashboard/chat/` | The two-panel chat screen |
| The "meaning" database itself | `infra/genai/pgvector/` | The manifests that create the pgvector database on the server |
| Secret keys (templates) | `infra/genai/secrets/` | Fill-in-the-blank templates; the real values are kept off GitHub |
| Deploy steps | `scripts/deploy/pgvector.sh`, `scripts/deploy/genai_api.sh`, the AI section of `scripts/deploy/airflow_pods.sh` | Automated install steps that run only when the switch is on |
| Install / uninstall guide | `genai/README.md` | How to turn the layer on, off, or remove it entirely |
| Design decisions | `docs/decisions/` | Short notes explaining *why* key choices were made |

---

## Frequently asked

**Is any of this costing money right now?**
No. The master switch is off by default, so no AI calls are made and no extra server resources run.

**Could turning it on break the existing pipeline or dashboard?**
No. The AI layer is fully separated and gated. With the switch off it's invisible; with it on, the existing features keep working exactly as before.

**Will the AI invent answers?**
The design specifically prevents that. The chat feature answers only from passages found in this project's own data, and it shows those sources next to every answer.

**What's the very next step?**
Run the live check for the chat tab, then start the rebuild described under "What comes next."
