# Incident: My RSA Key-Pair Migration Immediately Broke Itself

**Date:** 2026-05-10
**Severity:** High (full outage — dashboard, Airflow DAGs, dbt, anomaly detector all down)
**Status:** Resolved — see fix below

---

## Summary (the one-paragraph version)

The RSA key-pair migration I shipped yesterday was blocking its own logins today. Every Snowflake-touching component was down with the exact same "Can't reach Snowflake servers — check network connectivity." error I'd just seen during the MFA outage — which made me think something new had broken. It hadn't. The cause was a leftover auth policy from my May 9 MFA patch: `PIPELINE_SERVICE_ACCOUNT_POLICY` was still attached to `PIPELINE_USER`, and that policy had `AUTHENTICATION_METHODS = ('PASSWORD')` hardcoded into it. Once the pipeline switched to RSA key-pair auth, Snowflake looked at the policy and said "this user is only allowed to log in with a password" — and rejected every connection. Two SQL statements in Snowsight (UNSET the policy, then DROP it) fixed it immediately, no redeploy required.

---

## What Happened

I'd just deployed the RSA key-pair migration (see [2026-05-09-pipeline-user-rsa-keypair-migration.md](2026-05-09-pipeline-user-rsa-keypair-migration.md)) and the deploy succeeded. But after it finished, the dashboard was showing the same orange error panels I'd seen during the MFA outage yesterday:

> Can't reach Snowflake servers — check network connectivity.

Same message on every chart panel. Airflow tasks were also failing. The deploy script had confirmed the K8s secrets were applied correctly, the pod was running, and the `.p8` file was mounted — so something was wrong at the Snowflake auth level, not the infrastructure level.

I ran a one-shot connection test from inside the Airflow scheduler pod to get the actual error:

```bash
kubectl exec -it -n airflow-my-namespace $(kubectl get pods -n airflow-my-namespace -l component=scheduler -o name | head -1) -- \
  /opt/ml-venv/bin/python3 -c "
import snowflake.connector, os
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
key_path = os.environ['SNOWFLAKE_PRIVATE_KEY_PATH']
with open(key_path, 'rb') as f:
    p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
pk_bytes = p_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption())
c = snowflake.connector.connect(
    account=os.environ['SNOWFLAKE_ACCOUNT'],
    user=os.environ['SNOWFLAKE_USER'],
    private_key=pk_bytes,
    database=os.environ['SNOWFLAKE_DATABASE'],
    warehouse=os.environ['SNOWFLAKE_WAREHOUSE'],
    role='PIPELINE_ROLE')
print(c.cursor().execute('SELECT CURRENT_USER(), CURRENT_ROLE()').fetchone())
"
```

The error came back immediately:

> `snowflake.connector.errors.DatabaseError: 250001 (08001): Failed to connect to DB: qztxwkd-lsc26305.snowflakecomputing.com:443. Authentication attempt rejected by the current authentication policy.`

That last sentence is the key: **the current authentication policy.** The same errno 250001 I'd seen yesterday, but a completely different root cause hiding behind it.

---

## The Wrong Hypothesis First

My first guess was that the key file wasn't loading correctly — maybe the PEM parsing was silently falling back to a password handshake that then triggered the MFA mandate. But the error says "current authentication policy," not "multi-factor authentication is required." Those are two different rejection paths.

Then I looked at what authentication policies were still attached to `PIPELINE_USER`. In Snowsight as ACCOUNTADMIN:

```sql
DESC USER PIPELINE_USER;
```

The `AUTHENTICATION_POLICY` column showed: `PIPELINE_DB.PUBLIC.PIPELINE_SERVICE_ACCOUNT_POLICY`

That's the policy I created yesterday to exempt `PIPELINE_USER` from MFA while it was still on password auth. I left it in place as a safety net when I shipped the RSA migration — thinking it would be harmless since it just exempted the user from MFA. But looking at how I defined that policy:

```sql
CREATE OR REPLACE AUTHENTICATION POLICY PIPELINE_SERVICE_ACCOUNT_POLICY
    AUTHENTICATION_METHODS = ('PASSWORD')
    MFA_ENROLLMENT         = OPTIONAL
```

`AUTHENTICATION_METHODS = ('PASSWORD')` means "this user can **only** log in with a password." The moment I switched the pipeline to RSA key-pair auth, that policy became an active blocker — not a safety net. Snowflake sees the KEYPAIR login attempt, checks the policy, and rejects it before the key even gets verified.

The fix was obvious once I saw it: the policy was designed to solve a problem (MFA blocking an unattended robot account) that no longer exists when you're on key-pair auth. Key-pair auth is MFA-exempt by design. The policy needed to go.

---

## Fix

### In Snowsight (ACCOUNTADMIN role) — this is the actual unblocker

Ran these one at a time and verified between each:

```sql
-- Remove the auth policy so PIPELINE_USER can log in with KEYPAIR again
ALTER USER PIPELINE_USER UNSET AUTHENTICATION POLICY;

-- Confirm: AUTHENTICATION_POLICY should now be blank; RSA_PUBLIC_KEY_FP should still be set
DESC USER PIPELINE_USER;
```

```sql
-- Drop the policy object entirely — it was only ever needed for the password-auth era
DROP AUTHENTICATION POLICY PIPELINE_DB.PUBLIC.PIPELINE_SERVICE_ACCOUNT_POLICY;

-- Confirm: policy should not appear in the list
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

After the UNSET, logins started working immediately — no pod restart or redeploy needed.

### Code changes — committed alongside this doc

**`scripts/snowflake_setup.sql`** — section 11 was the block that created and attached the auth policy. I replaced the entire block with a comment explaining why it was removed, so anyone running `--snowflake-setup` on a fresh account doesn't accidentally recreate the problem:

```sql
-- 11. (REMOVED — 2026-05-10)
--     PIPELINE_SERVICE_ACCOUNT_POLICY was removed because its AUTHENTICATION_METHODS = ('PASSWORD')
--     clause blocked KEYPAIR logins after the RSA migration. Key-pair auth is MFA-exempt by design,
--     so the policy is no longer needed. See: 2026-05-10-auth-policy-blocking-keypair-login.md
```

**`dashboard/db.py:_classify_snowflake_error`** — errno 250001 is overloaded: it fires for both real network failures ("Could not connect to DB") and for auth-policy rejections ("Authentication attempt rejected by the current authentication policy"). The old classifier always mapped 250001 to `network_error`, which was the misleading "Can't reach Snowflake servers" headline I kept seeing. I added a string check before the 250001 branch so policy rejections get their own specific status:

```python
# Check the message before falling through to network_error —
# errno 250001 fires for both real network failures AND auth-policy rejections.
elif "authentication policy" in msg_lower:
    status = "auth_policy_rejected"
elif errno in (250001, 250003) or "could not connect" in msg_lower:
    status = "network_error"
```

**`dashboard/callbacks.py:_SNOWFLAKE_ERROR_MESSAGES`** — added the corresponding human-readable headline for the new status:

```python
"auth_policy_rejected": "Snowflake auth policy rejected the login — check the policy attached to PIPELINE_USER.",
```

**`docs/incidents/snowflake/2026-05-10-auth-policy-blocking-keypair-login.md`** — this file.

**`docs/incidents/INDEX.md`** — added this incident to the Snowflake table.

---

## Verification

1. **Re-ran the pod diagnostic** — the same Python snippet that had returned errno 250001 now returned `('PIPELINE_USER', 'PIPELINE_ROLE')`.

2. **Reloaded the dashboard** in a browser — every chart panel rendered with real data. The orange "Can't reach Snowflake servers" banners were gone.

3. **Triggered both consumer DAGs** — `dag_stocks_consumer` and `dag_weather_consumer` ran end-to-end. The dbt step completed (proving `private_key_path` in `profiles.yml` works) and the anomaly detector completed (proving the ml-venv direct connector works).

4. **Checked Snowflake login history:**

```sql
SELECT user_name, first_authentication_factor, error_message, event_timestamp
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE user_name = 'PIPELINE_USER'
ORDER BY event_timestamp DESC
LIMIT 10;
```

Recent rows show `RSA_KEYPAIR` for `first_authentication_factor` and no `error_message` — confirms logins are going through cleanly on key-pair auth.

---

## What's Still Left To Do

The password is still set on `PIPELINE_USER` and `SNOWFLAKE_PASSWORD` is still in the K8s secrets and GitHub Actions — but nothing uses them anymore. That's harmless for now. Retiring the password itself (UNSET PASSWORD in Snowflake, remove from K8s secret, remove from GitHub secret, remove the dead references from `dashboard/config.py` and `dashboard/db.py`) is a clean-up task worth ~15 minutes and is the right next step after this incident settles.

---

## Lessons

- **"Safety net" leftovers can have side effects.** I intentionally left `PIPELINE_SERVICE_ACCOUNT_POLICY` attached to `PIPELINE_USER` as a fallback during the RSA migration, not realizing that `AUTHENTICATION_METHODS = ('PASSWORD')` was an allowlist, not just an MFA setting. Before completing a migration, it's worth auditing all existing auth objects on the user — not just the credential itself.

- **errno 250001 is not specific enough to diagnose on its own.** The old classifier mapped it straight to `network_error` because that's the typical case. But Snowflake uses the same errno for at least two structurally different problems: actual TCP/DNS failures, and auth-policy rejections. The fix in `_classify_snowflake_error` now inspects the message text first so the dashboard headline is actually meaningful when this happens again.

- **The raw driver message is the real signal.** The new errno+message line added to every error figure in the May 9 deploy was what immediately pointed me at the auth policy. Without it the dashboard would have said "Can't reach Snowflake servers — check network connectivity" and I'd have spent time debugging infrastructure.

- **When the headline says "network error" but the hostname is still resolving, run a direct connection test from inside the pod first.** The one-shot Python snippet told me exactly what was wrong in under a minute.
