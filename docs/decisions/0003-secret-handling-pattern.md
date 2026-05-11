# ADR 0003: Secret-Handling Pattern (Committed Template + Gitignored Real File)

- Status: Accepted
- Date: 2026-05-10

## Context

The AI layer needs an API key — a long string of characters that authenticates requests to the AI service. This key costs real money if leaked: anyone who finds it can make requests billed to the account.

The project uses a Kubernetes cluster (a system that runs and manages containers on the server). Kubernetes has a built-in feature called a "Secret" — an encrypted file that pods (running containers) can read as environment variables. The question is: how do I get the key into that Secret without ever writing it in a file that could end up on GitHub?

Two patterns are already in use in this project:

1. **Committed template + gitignored real file** (`snowflake-secret.yaml`): a template showing the structure is committed; the real file with actual values lives only on the Mac and on the server, never in git.
2. **Binary file via `kubectl create secret --from-file`** (`snowflake-rsa-key`): used for the Snowflake RSA private key, a binary file that cannot be embedded in YAML.

## Decision

I used pattern 1 (committed template + gitignored real file) for the GenAI credentials secret.

The committed file, `genai-secrets.yaml.template`, shows exactly what the secret looks like and which values are needed. It uses obvious placeholders (`<BASE64_ENCODED_API_KEY>`) so anyone setting up the project knows what to fill in. The real file, `genai-secrets.yaml`, is listed in a `.gitignore` inside the same folder so it cannot accidentally be committed.

At deploy time, `sync.sh` copies the real file to the server and applies it to the cluster with `kubectl apply -f`. If the file is missing (e.g., the project is being set up for the first time), the deploy step prints a clear skip message and continues — the same behaviour as the Snowflake secret step.

Pattern 2 (the `--from-file` approach) is appropriate for binary files like RSA private keys because those cannot be embedded in a YAML value field. The GenAI credentials are plain text (API key string, provider name, model name), so YAML embedding is fine.

## Alternatives considered

**Store secrets in `.env.deploy` and create the K8s Secret inline via `kubectl create secret --from-literal`** (as used for `flask-app-secrets`). This works but means the API key must live in `.env.deploy` — a file that contains many other deploy settings and is slightly more likely to be accidentally shared or printed in logs.

**Use a dedicated secrets manager (AWS Secrets Manager, HashiCorp Vault).** More secure for a team environment. Significant extra infrastructure for a solo portfolio project running on a single server.

**Use `envsubst` to render the template before applying.** The YAML template would contain `${LLM_API_KEY}` and `envsubst` would substitute values at deploy time. Adds a new pattern not used elsewhere in the project, and requires the key to be available as a shell variable during deploy anyway.

## Consequences

**Wins:**
- The pattern is already established for Snowflake. Anyone reading the deploy scripts sees a familiar approach.
- The template is committed, so it serves as documentation of what credentials the project needs.
- The real secret file never appears in git history, even if the developer forgets to gitignore it — the `.gitignore` in the secrets folder handles it automatically.

**Trade-offs:**
- The real YAML file must be created manually from the template before the first deploy. There is no automation for this step because it requires typing in a real API key.
- If the developer loses the local copy of the real YAML (e.g., new laptop), they must recreate it from the template.
