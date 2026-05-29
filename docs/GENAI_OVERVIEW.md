# GenAI Layer — Plain-English Overview

> This page is written for **anyone**, technical or not. It answers two questions:
> 1. **The big picture** — what the AI features are meant to do for this project (not a step-by-step plan).
> 2. **What's actually built so far** — and where each piece lives.
>
> For the detailed, step-by-step engineering plan, see `GENAI_ROADMAP.md` (kept in the folder just outside the code repository). For install/uninstall instructions, see `genai/README.md`.

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
- **Swappable AI brain.** The project isn't locked into one AI company. Today it uses Anthropic's Claude; switching to OpenAI or a free local model is a one-line setting change.
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

The work is organized into 11 numbered stages ("EPICs"). Here's the honest status today.

| Stage | What it delivers | Status |
|------|------------------|--------|
| **1. Foundation + AI connection** | The on/off switch, the settings file, and a working connection to the AI (Anthropic, with OpenAI ready as a backup) | ✅ **Done & verified** |
| **2. The "meaning" database + AI toolbox** | The special search database (pgvector) and all the AI software libraries the server needs | 🟡 **Built — final verification pending** |
| 3. Filing reader | Downloads a company's full annual report and splits it into named sections | ⬜ Not started |
| 4. Filing fact-extractor | Sends those sections to the AI and saves the key facts to the database | ⬜ Not started |
| 5. Weather summaries | The optional weather-paragraph step | ⬜ Not started |
| 6. "Meaning fingerprint" maker | Turns any text into numbers that capture its meaning | ⬜ Not started |
| 7. Loader | Loads filing text and summaries into the search database | ⬜ Not started |
| 8. Search engine | Finds the most relevant passages for a question (keyword + meaning combined) | ⬜ Not started |
| 9. The decision-maker | The AI "brain" that picks which tools to use and writes the final answer | ⬜ Not started |
| 10. Chat tab | The two-panel chat screen on the website | ⬜ Not started |
| 11. The exam | Automatic answer-grading and score tracking | ⬜ Not started |

**In short:** the foundation is finished, the database and AI libraries are in place (just needing a final "yes, it's running" check), and the four headline features (stages 3–11) are the road ahead.

---

## Where each finished piece lives

Everything is kept in self-contained folders so the whole AI layer can be removed by deleting them and flipping the switch off.

| Piece | Where it lives | What it is, in plain terms |
|------|----------------|----------------------------|
| The on/off switch & all settings | `genai/config.py` | One file that holds the master switch, which AI to use, and the spending caps |
| The AI connection | `genai/llm/` | The code that actually talks to the AI service. `anthropic_provider.py` is the default; `openai_provider.py` is the ready-made backup; `_factory.py` picks the right one based on the setting |
| The search-database client | `genai/retrieval/pgvector_client.py` | The code that connects to the "meaning" database safely and efficiently |
| The "meaning" database itself | `infra/genai/pgvector/` | The setup files (manifests) that create the pgvector database on the server, including which tables it holds |
| Secret keys (templates) | `infra/genai/secrets/` | Fill-in-the-blank templates for the AI API key and database password (the real, filled-in versions are kept off of GitHub) |
| Deploy steps | `scripts/deploy/pgvector.sh` and the AI section of `scripts/deploy/airflow_pods.sh` | The automated steps that install the database and AI libraries onto the server — but only when the switch is on |
| Plain-English code comments | Throughout every file above | Each file opens with a short, jargon-free explanation of what it does and why |
| Install / uninstall guide | `genai/README.md` | How to turn the layer on, off, or remove it entirely |
| Design decisions | `docs/decisions/0001`–`0004` | Short notes explaining *why* key choices were made (the on/off switch, the swappable AI, how secrets are handled, caching) |

---

## Frequently asked

**Is any of this costing money right now?**
No. The master switch is off by default, so no AI calls are made and no extra server resources run.

**Could turning it on break the existing pipeline or dashboard?**
No. The AI layer is fully separated and gated. With the switch off it's invisible; with it on, the existing features keep working exactly as before.

**Will the AI invent answers?**
The design specifically prevents that. The chat feature answers only from passages found in this project's own data, and it shows those sources next to every answer.

**What's the very next step?**
Confirm Stage 2 is running on the server (the database pod is up and the AI libraries import correctly), tick its checkboxes in `GENAI_ROADMAP.md`, then begin Stage 3 (the filing reader).
