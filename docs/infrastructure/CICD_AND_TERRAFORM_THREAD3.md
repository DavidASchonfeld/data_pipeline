# Thread 3 — README Polish + Architecture Diagram + Docs Audit

## What was added

### 1. `README.md` — Cost callout section

Added a `## Cost` section immediately after the status block (before "What It Does") with the monthly cost breakdown:

- EC2 t3.large + 100 GiB EBS gp3 + Elastic IP ≈ $70–75/month
- Snowflake XSMALL auto-suspends after 60s; dashboard cache keeps activations to ~4/hour

This gives a portfolio viewer an immediate sense of real-world operating cost without needing to read the full README.

---

### 2. `README.md` + `docs/architecture/ARCHITECTURE_DIAGRAM.md` — Mermaid architecture diagram

Replaced the ASCII architecture block with a `flowchart LR` Mermaid diagram showing the full data flow:

```
External APIs (SEC EDGAR, Open-Meteo)
  → Apache Kafka topics (stocks-financials-raw, weather-hourly-raw)
  → Snowflake (RAW → STAGING → MARTS → ANALYTICS/FCT_ANOMALIES)
  → Flask + Dash Dashboard (:32147)
  → MLflow (experiment tracking branch)
Apache Airflow (orchestrates all, staleness monitor → Slack 60-min cooldown)
```

The Mermaid diagram renders natively in GitHub's Markdown preview. The standalone file at `docs/architecture/ARCHITECTURE_DIAGRAM.md` includes a component notes table and links back to README.

---

### 3. `README.md` — Public dashboard URL placeholder

Added a "Public dashboard (no tunnel required)" block after the SSH tunnel section:

```
http://<ELASTIC_IP>:32147/dashboard/
```

With a note to retrieve the IP via `terraform output elastic_ip` (run from `terraform/`). This makes the public access path immediately visible without digging into Terraform outputs.

---

### 4. `README.md` — dbt docs generation snippet

Added a `dbt docs generate && dbt docs serve` snippet under the Tech Stack table. This is a standard dbt workflow step that portfolio reviewers often look for; surfacing it here reduces friction for anyone evaluating the project.

---

### 5. `README.md` — Slack alerting expanded

Expanded the alerting bullet in Key Features from:
> "data staleness monitor (30 min); Slack webhook supported"

To explicitly name Slack webhook + 60-minute cooldown:
> "data staleness monitor (every 30 min) fires a Slack webhook with a 60-minute cooldown so repeated alerts don't flood the channel; configure via `SLACK_WEBHOOK_URL` in your K8s secrets"

---

### 6. Docs rename: `docs/plain-english/` → `docs/guides/` and `docs/PLAIN_ENGLISH_GUIDE.md` → `docs/GUIDES.md`

Renamed the plain-english guide directory and top-level file to use the shorter, more intuitive `guides/` name. All internal links updated across:

| File | Change |
|------|--------|
| `README.md` | Footer link updated to `docs/guides/` |
| `docs/INDEX.md` | Section header + 10 path references updated |
| `docs/GUIDES.md` (was PLAIN_ENGLISH_GUIDE.md) | H1 + 9 internal part links updated |
| `docs/guides/README.md` (was plain-english/README.md) | H1 updated |
| `docs/reference/KUBECTL_COMMANDS.md` | Line 17 comment: `PLAIN_ENGLISH_GUIDE.md` → `docs/GUIDES.md` |
| `scripts/bootstrap_ec2.sh` | Line 230 comment: `PLAIN_ENGLISH_GUIDE.md Bugs 7–9` → `docs/GUIDES.md Part 4–5` |

> **To complete this rename with git history preserved, run:**
> ```bash
> git mv docs/plain-english docs/guides
> git mv docs/PLAIN_ENGLISH_GUIDE.md docs/GUIDES.md
> ```
> All file content edits have already been applied; only the filesystem rename remains.

---

### 7. `docs/architecture/failure-modes/airflow-exec-oom-kill.md` — wording fix

Changed line 15 from:
> "Previous attempts focused on increasing timeouts and CPU requests."

To:
> "Earlier approaches focused on increasing timeouts and CPU requests."

Removes tool-name reference from production documentation.

---

## Design Decisions

**Mermaid over ASCII art**
GitHub renders Mermaid diagrams natively in Markdown. The ASCII diagram was readable in a terminal but displayed as a monospace wall of text on GitHub. Mermaid provides the same structural information with visual hierarchy — boxes, arrows, subgraphs — which is materially better for portfolio presentation.

**`git mv` for the rename**
Using `git mv` rather than creating new files preserves the full commit history of every file in `docs/plain-english/`. Future `git log docs/guides/07-alerting.md --follow` will still show all prior edits. Deleting and recreating would orphan that history.

**`docs/GUIDES.md` naming**
"Guides" is shorter and more standard than "Plain English Guide" as a directory/file name. It also better reflects the content now that several parts cover technical topics (Snowflake write failure, dbt, MLflow) rather than only plain-English explanations.

**Standalone `ARCHITECTURE_DIAGRAM.md`**
Having the diagram in a dedicated file means it can be linked from other docs (runbooks, failure modes) without embedding the README. It also keeps the README from growing excessively large.

---

## Snowflake Charges — Read This

None. All changes in this thread are documentation-only. No Snowflake queries, no warehouse activations, no infrastructure changes.

---

## Manual Steps Required

### Step A — Complete the directory rename with git history preserved

```bash
# Run these two commands from the repo root to preserve git history
git mv docs/plain-english docs/guides
git mv docs/PLAIN_ENGLISH_GUIDE.md docs/GUIDES.md
```

All file content has already been updated. These two commands complete the filesystem rename so links resolve correctly.

### Step B — Verify Mermaid renders on GitHub

Push to your branch and open the README on GitHub. The architecture diagram block should render as a flowchart, not a code block. If it shows as plain text, confirm the opening fence is ` ```mermaid ` (not ` ```markdown `).
