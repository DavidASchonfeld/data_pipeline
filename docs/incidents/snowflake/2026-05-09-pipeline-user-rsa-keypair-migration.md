# Upgrade: Migrating `PIPELINE_USER` to RSA Key-Pair Authentication

**Date:** 2026-05-09
**Severity:** Planned upgrade (no outage — production stayed up the entire time)
**Status:** Phase 1 shipped; Phase 2 cleanup tracked in [docs/TODO.md](../../TODO.md)

---

## Summary (the one-paragraph version)

Earlier today I patched my Snowflake MFA outage with a special "authentication policy" that exempted my service account, `PIPELINE_USER`, from MFA. That worked, but it left a real password sitting in my Kubernetes secrets and a one-off MFA exemption rule sitting in my Snowflake account. Both felt like the kind of leftover that quietly bites you six months later. So I replaced password authentication with **RSA key-pair authentication** — the way real production data teams run their service accounts. Every component of the pipeline (Airflow, dbt, the anomaly detector, the Flask dashboard, and CI) now logs in with a private key file instead of a password. The password is still on the user as a safety net for now; retiring it is a small follow-up tracked in [docs/TODO.md](../../TODO.md).

---

## What is an RSA key-pair, in plain language?

Think of a public key as a padlock and the matching private key as the only key that opens it. You can hand out copies of the padlock freely — anyone can use it to lock a box, but only the person holding the key can open it.

RSA key-pair login works the same way. I gave Snowflake the padlock (the **public** key) and stored the matching key (the **private** key) on my pipeline. When the pipeline asks Snowflake to let it in, it doesn't send the key over the wire — it proves it has the key by signing a small challenge that Snowflake can only verify with the matching padlock. The actual private key never leaves the pipeline. That's the big win over passwords, which travel across the network on every single login and can be leaked or harvested if anything along the way is compromised.

---

## Why this is better than the May fix

- **No password ever touches the wire on a pipeline login.** A signed challenge proves identity instead.
- **No MFA-exemption carve-out is needed.** Snowflake's MFA mandate applies to password logins; key-pair auth skips MFA by design. So I don't have to maintain a special `PIPELINE_SERVICE_ACCOUNT_POLICY` just to keep my robot account working.

Key-pair auth is also the pattern Snowflake itself recommends for service accounts in their official docs.

---

## What changed in the project

The dashboard, the Airflow DAGs, dbt, the anomaly detector, and the GitHub Actions CI workflow all now log in with a key file instead of a password. Concretely:

- The private key file (`rsa_key.p8`) is stored as a Kubernetes secret named `snowflake-rsa-key`.
- That secret is mounted **read-only** into every pod that talks to Snowflake, at `/secrets/snowflake/rsa_key.p8`.
- An environment variable, `SNOWFLAKE_PRIVATE_KEY_PATH`, points the application code at that file.
- In CI, GitHub Actions writes the key to a temp file at job start and points the same env var at it.

Files that changed:

- `airflow/dags/anomaly_detector.py` — direct Snowflake connector, now passes `private_key=` instead of `password=`.
- `dashboard/db.py` and `dashboard/config.py` — SQLAlchemy engine for the Flask dashboard.
- `profiles.yml` — dbt profile, now uses `private_key_path:` instead of `password:`.
- `scripts/deploy/sync.sh` — builds the Airflow Snowflake connection JSON with `private_key_file` in extras, and uploads the .p8 file to EC2 as a Kubernetes secret.
- `airflow/helm/values.yaml` and `dashboard/manifests/pod-flask.yaml` — mount the new secret into the right pods.
- `.github/workflows/dbt-test.yml` — writes the key from a GitHub Actions secret to a temp file before running `dbt test`.

---

## How I generated and installed the key

Two `openssl` commands on my laptop. The `-nocrypt` flag means the private key file isn't password-protected (the file itself sits inside an encrypted Kubernetes secret + a 600-permissioned file on my machine, so layering a passphrase on top adds operational pain without a real security gain at this stage):

```bash
mkdir -p ~/.snowflake
cd ~/.snowflake
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out pipeline_user_rsa.p8 -nocrypt
openssl rsa -in pipeline_user_rsa.p8 -pubout -out pipeline_user_rsa.pub
chmod 600 pipeline_user_rsa.p8
```

Then in a Snowsight worksheet, as `ACCOUNTADMIN`, I ran one statement to register the public key on the user:

```sql
ALTER USER PIPELINE_USER SET RSA_PUBLIC_KEY='<contents of pipeline_user_rsa.pub, no header/footer lines, no newlines>';
```

The private key file (`~/.snowflake/pipeline_user_rsa.p8`) is gitignored and lives only on my laptop and on EC2. The deploy script `scp`s it to EC2 and creates the Kubernetes secret from it.

---

## How to rotate the key in the future

Snowflake supports two public keys on a user at the same time, which makes rotation zero-downtime:

1. Run `openssl` again to generate a new keypair.
2. Run `ALTER USER PIPELINE_USER SET RSA_PUBLIC_KEY_2='<new public key>';` — both keys are now valid.
3. Update the local `.p8` file and redeploy. The pipeline starts using the new key.
4. Once everything is verified, run `ALTER USER PIPELINE_USER UNSET RSA_PUBLIC_KEY;` to retire the old one. Optionally then promote the new key from `_2` to the primary slot.

---

## What's still left to do

The password is still set on `PIPELINE_USER`, the `SNOWFLAKE_PASSWORD` env var is still in my K8s secret, and `PIPELINE_SERVICE_ACCOUNT_POLICY` is still attached to the user. Nothing uses them anymore — every component is now on key-pair auth — but I left them in place as a safety net while I confirm the migration is clean. Removing all three is a small follow-up: see the **"Retire the Snowflake password and MFA-exemption auth policy"** entry in [docs/TODO.md](../../TODO.md).

---

## How I verified it worked

1. **Smoke test from inside an Airflow scheduler pod:** ran a one-shot Python snippet that loads the key file and connects, asking Snowflake for `current_user()` and `current_role()`. Got back `('PIPELINE_USER', 'PIPELINE_ROLE')`.
2. **Triggered both consumer DAGs end-to-end** — `dag_stocks_consumer` and `dag_weather_consumer`. The dbt step ran (which proved dbt's `private_key_path` works) and the anomaly detector ran (which proved the direct Snowflake connector in the ML venv works).
3. **Reloaded the dashboard** in a browser — every chart panel rendered, which proved the SQLAlchemy `connect_args={"private_key": …}` path works.
4. **Opened a small CI PR** that touched `airflow/dags/dbt/` to trigger the `dbt-test` workflow. It passed.
5. **Checked Snowflake's login history** with:
   ```sql
   SELECT user_name, first_authentication_factor, event_timestamp
   FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
   WHERE user_name = 'PIPELINE_USER'
   ORDER BY event_timestamp DESC
   LIMIT 20;
   ```
   Recent rows show `RSA_KEYPAIR` instead of `PASSWORD` — the actual confirmation that the pipeline is logging in with the key, not falling back to the password.

---

## Closing

This is the long-term fix I promised at the bottom of [2026-05-09-snowflake-mfa-blocking-pipeline.md](2026-05-09-snowflake-mfa-blocking-pipeline.md). The May 9 outage forced the issue earlier than I planned — but the path forward (RSA key-pair auth) is the same one Snowflake recommends for service accounts in production, and now my pipeline runs that way.
