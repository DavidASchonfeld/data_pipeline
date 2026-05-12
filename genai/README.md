# GenAI Layer

This folder contains the AI features built on top of the data pipeline: structured extraction of filing text, weather summaries, a retrieval-based chat interface, and an answer-quality harness.

## Is it active?

Check the `GENAI_ENABLED` environment variable in `.env.deploy`. If it is `false` (the default), none of this code runs and the pipeline behaves exactly as it did before this folder existed.

---

## How to turn it on

1. Get an API key from your chosen AI provider (default: Anthropic — sign up at `console.anthropic.com`).
2. **API key permissions**: choose **Restricted** and enable only the single permission the pipeline needs for inference calls. Avoid "All" — if the key is ever leaked, restricting it limits the blast radius to inference credits only, not account or billing access.
   - **OpenAI**: `Model capabilities` → `Chat completions (/v1/chat/completions)` → **Write**. Every other sub-permission (Responses, Embeddings, Images, Assistants, Fine-tuning, Files, etc.) stays **None**.
   - **Anthropic**: enable only the messages (inference) scope.
3. Set a monthly spend cap in the provider's console (recommended: $10).
4. Add the following to `.env.deploy`:
   ```
   GENAI_ENABLED="true"
   LLM_PROVIDER="anthropic"
   LLM_MODEL="claude-sonnet-4-5"
   LLM_API_KEY="sk-ant-..."
   ```
5. Create the real K8s secret file from the template:
   ```bash
   cp infra/genai/secrets/genai-secrets.yaml.template infra/genai/secrets/genai-secrets.yaml
   # base64-encode each value and paste it in
   echo -n "sk-ant-..." | base64
   ```
6. Run `./scripts/deploy.sh`.

---

## How to turn it off

Set `GENAI_ENABLED="false"` in `.env.deploy` and run `./scripts/deploy.sh`. No AI calls will be made and no extra resources will be deployed. The source files stay in place but are completely inactive.

---

## How to remove it entirely (full purge)

1. Set `GENAI_ENABLED="false"` and redeploy.
2. Delete the `genai/`, `airflow/dags/genai_dags/`, `dashboard/chat/`, and `infra/genai/` folders.
3. Remove the single `# genai` block from `scripts/deploy/sync.sh`.
4. Remove the single `# genai` block from `dashboard/app.py` (added in a later epic).
5. (Optional) Drop the Snowflake tables: `ANALYTICS.FCT_FILING_EXTRACTS`, `MARTS.FCT_WEATHER_SUMMARIES`.

---

## How to swap the AI provider

### Supported providers

| `LLM_PROVIDER` | `LLM_MODEL` example | Notes |
|---|---|---|
| `anthropic` | `claude-sonnet-4-5` | Default |
| `openai` | `gpt-4o-mini` | Implemented — use as a drop-in when Anthropic is unavailable |

### Steps

1. Set `LLM_PROVIDER` and `LLM_MODEL` in `.env.deploy`.
2. Update `LLM_API_KEY` to the new provider's key (same rule applies: Restricted, inference-only permission — see step 2 of "How to turn it on" for provider-specific details).
3. Redeploy.

No other code changes are needed — all callers go through `get_llm_provider()`.

### Adding a new provider

1. Create `genai/llm/<name>_provider.py` implementing `LLMProvider` (use `anthropic_provider.py` as a template).
2. Add one branch to `genai/llm/_factory.py`.
3. Add the name to `_SUPPORTED` in `_factory.py`.
4. Follow the steps above.

---

## Folder layout

```
genai/
├── config.py            # all environment variable reads for this layer
├── llm/
│   ├── base.py          # the interface every provider must implement
│   ├── _factory.py      # returns the right provider based on LLM_PROVIDER
│   ├── anthropic_provider.py
│   ├── openai_provider.py
│   └── __tests__/       # offline and live tests for the provider
├── __tests__/           # tests for config.py
└── README.md            # this file
```

Later epics add `extraction/`, `embedding/`, `retrieval/`, `runners/`, `agent/`, and `eval/` subfolders here.

---

## Cost guardrails

- `GENAI_MAX_COST_PER_QUERY` (default `$0.05`) — maximum per-question AI spend, enforced by the chat agent.
- `GENAI_DAILY_BUDGET` (default `$2.00`) — maximum daily AI spend, enforced by the chat agent.
- The AI provider's own console budget cap is the final safety net (recommended: $10/month).
