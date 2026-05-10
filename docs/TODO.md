# Deferred work

Tracking list of follow-up tasks that aren't urgent but should be done eventually.

---

## Migrate `PIPELINE_USER` from password to RSA key-pair auth

**Status:** Deferred (added 2026-05-09)
**Priority:** Medium — current setup works; this is a hardening upgrade
**Effort:** ~30–60 min
**Origin:** [snowflake/2026-05-09-snowflake-mfa-blocking-pipeline.md](incidents/snowflake/2026-05-09-snowflake-mfa-blocking-pipeline.md)

### Why

Today I unblocked the pipeline by exempting `PIPELINE_USER` from Snowflake's MFA mandate via a service-account authentication policy. That works, but the production-grade pattern for service accounts is **RSA key-pair authentication** — a private cryptographic key signs each login, no password is involved, and key-pair auth is MFA-exempt by design.

Benefits over the current password + auth-policy setup:
- No password to leak, rotate, or forget.
- No special auth-policy exemption to maintain.
- Stronger by default — what production data teams (Netflix, Capital One, Instacart, etc.) actually use.
- A solid portfolio talking-point.

### Steps

1. **Generate an RSA keypair locally** (PKCS#8, unencrypted — the simplest form to load into the connector):
   ```bash
   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
   openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
   ```
   Treat `rsa_key.p8` like a password. Never commit it.

2. **Register the public key on `PIPELINE_USER`** in a Snowsight worksheet as ACCOUNTADMIN:
   ```sql
   ALTER USER PIPELINE_USER SET RSA_PUBLIC_KEY='<paste contents of rsa_key.pub WITHOUT the BEGIN/END lines>';
   ```

3. **Update the connection code** in `dashboard/db.py:21-44` to use `private_key=...` instead of `password=...`. The Snowflake Python connector accepts a `private_key` parameter (DER-encoded bytes).

4. **Update the Airflow connection blob** in `scripts/deploy/sync.sh:42-89` so the `AIRFLOW_CONN_SNOWFLAKE_DEFAULT` JSON uses `private_key_file` (path inside the pod) instead of `password`.

5. **Replace `SNOWFLAKE_PASSWORD` in the K8s secret** (`airflow/manifests/snowflake-secret.yaml`) with `SNOWFLAKE_PRIVATE_KEY` containing the contents of `rsa_key.p8`. Mount the secret as a file at a known path that step 4 references.

6. **Drop the auth-policy exemption** — once key-pair auth is in place, the policy is no longer needed:
   ```sql
   ALTER USER PIPELINE_USER UNSET AUTHENTICATION POLICY;
   DROP AUTHENTICATION POLICY PIPELINE_SERVICE_ACCOUNT_POLICY;
   ```
   Also remove section 11 from `scripts/snowflake_setup.sql`.

7. **Verify end-to-end:** dashboard renders, DAGs run, dbt builds — all without `SNOWFLAKE_PASSWORD` anywhere.

### Notes

- Key rotation is just step 1 + step 2 again. Snowflake supports `RSA_PUBLIC_KEY_2` for zero-downtime rotation.
- Optional: encrypt `rsa_key.p8` with a passphrase and pass the passphrase via a separate secret. Adds complexity; not required.
