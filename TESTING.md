# Tests

All commands below are run from the **repo root** — the `data_pipeline/` folder.

---

## Quick reference

| Command | Context | What it does | Touches real API? |
|---|---|---|---|
| `make test` | Local | Offline tests only — no network, no cost | No |
| `make test-all` | Local | Installs provider SDKs + runs all offline tests | **No** |
| `pytest -m live` | Local | Single live API call — validates key + both call paths | **Yes** |
| Automatic | During `./scripts/deploy.sh` | Offline tests on Mac then inside pod | No |
| Manual | On server, no redeploy | Re-run pod tests to spot-check a live deploy | No |

---

## Local development

### First-time setup — virtualenv

The first time you run `make test` or `make test-all`, Make automatically creates a `.venv/` folder in the repo root and installs dependencies there. You don't need to do anything — just run the command.

On subsequent runs Make skips this step because `.venv/bin/python3` already exists.

> You never need to activate the venv manually. The Makefile calls `.venv/bin/python3` and `.venv/bin/pip` directly.

If the venv ever gets corrupted or you want to start fresh, delete it — Make recreates it on the next run:
```bash
rm -rf .venv && make test
```

---

### First-time setup — credentials

Your local credentials live in **`data_pipeline/.env`** at the repo root. This file is gitignored and never committed.

> **Note on the two env files:**
> - `.env` — read by `pytest` on your Mac. Put your LLM key here for local testing.
> - `.env.deploy` — read by `deploy.sh` and rsynced to the server. Put your LLM key here so the deployed pod can make live calls.
> Put the LLM key in **both files** if you want `make test-all` locally *and* live calls from the server.

> **Common mistake:** Adding `LLM_API_KEY` to `.env.deploy` but not `.env`.
> The two files are completely independent — local pytest only reads `.env`.
> `.env.deploy` is for the server (`deploy.sh`); it does nothing for local tests.

Open `data_pipeline/.env` and add:

**Anthropic:**
```
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
```

**OpenAI:**
```
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o
```

`LLM_MODEL` is only required for OpenAI. Anthropic picks a default automatically.

### Offline tests (no API key needed)

```bash
make test
```

Runs every test in `tests/` and `genai/` except those marked `live`. Safe to run any time — no network, no cost.

One test (`test_configured_provider_is_deployment_ready`) also validates your `.env`:
- `LLM_PROVIDER` must be `anthropic` or `openai`
- OpenAI: `LLM_API_KEY` must be present and `LLM_MODEL` must not be a Claude model name

If anything is wrong it fails with a clear message.

### Full offline run — with provider SDKs installed

```bash
make test-all
```

Same tests as `make test`, but first installs the `openai` and `anthropic` packages so that any import-time checks against the real SDK objects pass. **Does not touch the real API.**

Run this after installing or upgrading the provider SDKs, or when you want the full suite without spending API credits.

### Live API check — verifies your key works

```bash
pytest -m live
```

Makes two real API calls (`complete()` and `chat()`) and confirms both return valid responses. Run this after:
- Setting up your API key for the first time
- Switching providers
- Making changes to `complete()` or `chat()` in `genai/`

Cost: ~$0.001 per run.

---

## During deploy (automatic — no action needed)

`./scripts/deploy.sh` runs tests at two points automatically:

| When | Where | What runs | On failure |
|---|---|---|---|
| **Pre-deploy** (Phase 1) | Your Mac | All offline tests — equivalent to `make test` | Aborts before any remote work |
| **Post-deploy** (Phase 5) | Scheduler pod | Offline genai tests inside the running container | Aborts deploy, prints fix hint |

Both rounds are always offline — no API calls, no cost. The post-deploy round only runs when `GENAI_ENABLED=true`.

---

## Manual server verification (no redeploy needed)

Re-run the pod tests directly to spot-check a live deploy without redeploying:

```bash
ssh ec2-stock "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /bin/bash -c 'cd /opt/airflow && python -m pytest genai/ -m offline -v --no-header 2>&1'"
```

To include live API calls from the pod (requires `LLM_API_KEY` in `.env.deploy` and already deployed):

```bash
ssh ec2-stock "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /bin/bash -c 'cd /opt/airflow && python -m pytest genai/ -v --no-header 2>&1'"
```

---

## How tests are categorized

| Marker | Meaning | `make test` | `make test-all` | `pytest -m live` | Deploy (pre) | Deploy (post-pod) |
|---|---|---|---|---|---|---|
| None | Always runs | Yes | Yes | No | Yes | Yes |
| `@pytest.mark.offline` | Explicitly offline | Yes | Yes | No | Yes | Yes |
| `@pytest.mark.live` | Real API call — key required | No | **No** | **Yes** | Never | Never |

Using an undeclared marker is a hard error — this prevents typos from silently skipping tests.

---

## Adding a new test

- **Offline test**: drop a file under `tests/` or `genai/` — picked up automatically.
- **Live test**: add `@pytest.mark.live` so it's excluded from the standard suite and all deploy checks.
- **New LLM provider**: add `genai/llm/__tests__/test_<name>_provider.py`, mirroring `test_openai_provider.py`. Also add the new provider name to `VALID_PROVIDERS` in `test_configured_provider_is_deployment_ready`.
