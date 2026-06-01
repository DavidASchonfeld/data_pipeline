# Incident: `--snowflake-setup` Blocked by a Phone Login Prompt (fixed with a key-based DEPLOY_USER)

**Date:** 2026-05-31
**Severity:** Medium — only the one-time database setup step (`./scripts/deploy.sh --snowflake-setup`) was blocked. The live pipeline, dashboard, and DAGs kept running normally. The real risk was latent: the same block would hit every future setup run and any from-scratch rebuild.
**Status:** Code fix shipped. One small manual step (creating the new login in Snowflake's website) is done by me once, then it never needs doing again.

---

## Summary (the one-paragraph version)

When I ran the database-setup step of my deploy, it failed immediately while trying to log in to Snowflake as an administrator. The error looked like a network problem ("could not connect"), but it wasn't. Snowflake now demands a phone tap (multi-factor authentication, or "MFA") on every password login — and my deploy script is an automated robot with no phone to tap. So the login timed out and the deploy gave up. The fix is the same trick already used elsewhere in this project: instead of a password, the script now logs in with a **digital key** (key-pair authentication), which Snowflake accepts without any phone prompt. I created a dedicated, password-free login called `DEPLOY_USER` that the deploy script uses, gave it that key, and pointed the script at it. Now the setup step runs start to finish with no phone, no password, and no website visit.

---

## What happened

I ran:

```bash
./scripts/deploy.sh --snowflake-setup
```

It failed within seconds, right at the first attempt to connect to Snowflake, with:

```
snowflake.connector.errors.OperationalError: 251012: Login request is retryable. Will be handled by authenticator
snowflake.connector.errors.OperationalError: 250001: Could not connect to Snowflake backend after 2 attempt(s). Aborting
```

The deploy log showed it never got past the very first line, `Connecting to Snowflake as ACCOUNTADMIN...`. Everything after that — creating tables, the GenAI setup — never ran.

**Plain-language version:** my deploy tried to "log in" to the database as the admin. Snowflake answered "before I let you in, prove it's really you by tapping your phone." There's no human watching a phone during an automated deploy, so after a couple of tries it gave up. The "Could not connect" wording made it look like the internet was down, which it wasn't — Snowflake was actually answering, it was just refusing the login.

---

## Root cause

In early 2026 Snowflake made MFA mandatory for password-based logins. That's good security for *human* logins, but it breaks *robot* logins — automated scripts have no phone to approve a prompt.

My setup script (`scripts/deploy/snowflake.sh`) was connecting as an administrator using a **username + password**:

```python
conn = snowflake.connector.connect(
    account=..., user=SNOWFLAKE_ADMIN_USER, password=SNOWFLAKE_ADMIN_PASSWORD, role="ACCOUNTADMIN",
)
```

Because that login is a password login, Snowflake tried to add the phone step, the script couldn't complete it, and the connection was rejected.

This is the **same root cause** as the earlier outage in [2026-05-09-snowflake-mfa-blocking-pipeline.md](2026-05-09-snowflake-mfa-blocking-pipeline.md) — but a different login. That incident was about `PIPELINE_USER`, the low-power account the *running pipeline* uses (fixed by moving it to a digital key in [2026-05-09-pipeline-user-rsa-keypair-migration.md](2026-05-09-pipeline-user-rsa-keypair-migration.md)). This one is about the *high-power administrator login* used only by the one-time setup step, which was still on password auth and so was still exposed to the MFA wall.

---

## Why this did not take down the live pipeline

The setup step (`--snowflake-setup`) only runs when I'm creating or updating database structures (warehouse, schemas, tables, logins). The pipeline's normal day-to-day work — dashboard, DAGs, dbt — uses the separate `PIPELINE_USER` login, which already authenticates with a digital key and is unaffected. So nothing was actually down. The danger was future-facing:

1. The next feature that needs a new database table via setup would hit the same wall and **abort the whole deploy**.
2. Rebuilding from scratch (a fresh Snowflake account) *requires* this admin login — so disaster recovery was quietly broken.

---

## Fix

The principle: stop logging the administrator in with a password. Use a **digital key** instead, which Snowflake accepts with no phone prompt. A key pair is two matching files — a private key I keep secret on my Mac, and a public key I hand to Snowflake. Snowflake checks that they match; that *is* the proof of identity, so there's nothing for a phone to add.

Rather than put a key on my personal admin login, I created a **dedicated login just for deploys** — cleaner and safer (explained under Lessons).

### 1. A shared "run this SQL file" helper that prefers key login — `scripts/deploy/apply_snowflake_sql.py` (new)

All three of the setup script's database steps were each opening their own password connection (copy-pasted three times). I replaced that with one small shared helper that every step calls. It logs in with the **digital key** when one is configured (`SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH`), and only falls back to the old password method if no key is set. So the change is backward-compatible — nothing breaks for anyone who hasn't switched yet.

### 2. The setup script now calls that helper — `scripts/deploy/snowflake.sh` (refactored)

`step_snowflake_setup` and the GenAI bootstrap now call `apply_snowflake_sql.py` instead of each running their own password login. The pre-check was updated to accept **either** a key path **or** a password, and it prints a clear hint pointing at the setup helper if neither is present.

### 3. A one-command setup helper — `scripts/deploy/setup_deploy_user.sh` (new)

Running this once does the automatable part for me: it generates the key pair locally and prints the exact Snowflake commands (with my real public key already filled in) plus the two settings lines to paste into `.env.deploy`. No guesswork.

### 4. Documented the new setting — `.env.deploy.example`

Added `SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH`, marked the key method as recommended, and noted that the password method is blocked by MFA.

### The one-time manual part (done in Snowflake's website)

Creating a login can only be done from inside Snowflake by an administrator, and that interactive login happens in the browser — where the phone prompt works fine. So once, signed in to Snowsight as `ACCOUNTADMIN`, I ran:

```sql
CREATE USER IF NOT EXISTS DEPLOY_USER
  RSA_PUBLIC_KEY = '<the public key the helper printed>'
  DEFAULT_ROLE   = ACCOUNTADMIN
  COMMENT = 'Headless bootstrap user for ./scripts/deploy.sh --snowflake-setup';
GRANT ROLE ACCOUNTADMIN TO USER DEPLOY_USER;
```

`DEPLOY_USER` is created with **no password at all** — it can *only* log in with the key, so it can never be phished or used by a person. Then in `.env.deploy` I switched the admin login over (old lines commented out, not deleted, with a backup at `.env.deploy.bak`):

```
SNOWFLAKE_ADMIN_USER=DEPLOY_USER
SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH=/Users/David/.snowflake/deploy_user_rsa_key.p8
```

---

## Verification

1. **Settings resolve correctly** — loading `.env.deploy` the way the deploy does, `SNOWFLAKE_ADMIN_USER` reads `DEPLOY_USER`, the key path points at a file that exists, and no admin password is set (so the key path is used).
2. **Next setup run** should print `Connecting to Snowflake as DEPLOY_USER (RSA key-pair, MFA-exempt)...` and complete with **no phone prompt**, then create `ANALYTICS.FCT_FILING_EXTRACTS` and `MARTS.FCT_WEATHER_SUMMARIES`.
3. **The old path still works** for anyone who hasn't switched — if `SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH` is blank, the script uses the password as before.

---

## Lessons / notes

- **Robots can't tap a phone.** Any automated login must use something a script can present on its own — a digital key — not a password that triggers a human prompt. This is the second time MFA has bitten this project; the lesson is to put *every* automated login on key authentication, not just the obvious one.
- **Two logins, two jobs, two privilege levels.** `PIPELINE_USER` (low power) runs inside the cluster and does day-to-day work. `DEPLOY_USER` (high power) runs only on my Mac during a deploy. Keeping them separate means the powerful credential never lives inside the cluster *or* on my personal account — only on my deploy machine.
- **A password-free login is a feature, not a limitation.** Because `DEPLOY_USER` has no password, there's nothing to leak or phish; the key is the only way in.
- **"Could not connect" lied again.** Just like the 2026-05-09 incident, the error wording pointed at the network when the real cause was the login policy. When Snowflake says "could not connect," read the *full* message — the words "retryable / authenticator" were the tell that it's an MFA/login issue, not a network one.
- **One browser visit, then never again.** Creating the login needs one interactive admin session (where the phone prompt is fine). After that, every deploy is fully hands-off.
