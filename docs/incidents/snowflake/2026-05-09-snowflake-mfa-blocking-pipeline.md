# Incident: Snowflake's MFA Mandate Blocked My Pipeline

**Date:** 2026-05-09
**Severity:** High (full dashboard outage; every Snowflake-touching component down — dashboard, Airflow DAGs, dbt, anomaly detector)
**Status:** Resolved

---

## Summary (the one-paragraph version)

After I redeployed the dashboard, every panel showed *"Can't reach Snowflake servers — check network connectivity."* I initially suspected my AWS Marketplace upgrade from a Snowflake free trial to Enterprise had created a brand-new account with a new locator (which would have meant rebuilding everything from scratch). It hadn't. The real cause was much simpler: **Snowflake recently made MFA mandatory for password-based logins**, and `PIPELINE_USER` — the service account my pipeline runs as — has no phone to approve an MFA prompt. Every connection the dashboard, Airflow, and dbt tried to open got rejected with a misleading errno that looked like a network failure. The fix was to create a Snowflake "authentication policy" that exempts `PIPELINE_USER` from MFA (while keeping MFA on my human login). About 5 minutes of SQL in a Snowsight worksheet, plus a redeploy.

---

## What Happened

After redeploying the dashboard, every chart panel showed:

> Can't reach Snowflake servers — check network connectivity.

Same message on the stocks page, weather page, and every Plotly figure on every panel. The dedicated `make_network_error_figure()` placeholder in `dashboard/chart_utils.py:61-63` was being used.

That message comes from `_classify_snowflake_error()` in `dashboard/db.py:78-100`, which classifies any errno **250001** ("Could not connect") or **250003** ("Failed to execute request") as `network_error`. So based on the message alone, this looked like a hostname resolution problem.

I had also just upgraded my Snowflake free trial to an Enterprise account purchased through AWS Marketplace, so my first guess was that the upgrade had created a new account with a new locator — meaning the K8s secret `airflow/manifests/snowflake-secret.yaml` (still pointing at `qztxwkd-lsc26305`) was now dialing a hostname that no longer existed.

That guess was wrong.

---

## The Two Wrong Hypotheses (and why I dropped them)

### Wrong hypothesis #1: AWS Marketplace created a brand-new account

I had logged into Snowsight and the URL bar showed `https://app.snowflake.com/us-east-1/adc94167/...`. `CURRENT_ACCOUNT()` returned `ADC94167`, `CURRENT_REGION()` returned `AWS_US_EAST_1`. I assumed `adc94167` was a new locator that replaced the old `qztxwkd-lsc26305`.

**Why I dropped it:** When I looked at the Snowsight account-picker dropdown, it still listed `QZTXWKD` as my organization and `LSC26305` as my account name. Those are the same identifiers as the old trial — meaning AWS Marketplace had upgraded my existing account in place, not created a new one. `ADC94167` is just the lower-level system locator for the same account; `qztxwkd-lsc26305` is the org-account form pointing at it. Both addresses go to the same place.

### Wrong hypothesis #2: Network connectivity / DNS

The dashboard literally said "network connectivity," so I considered whether the cluster's egress was broken. I dropped this within minutes by running a connection test directly from my laptop (which has independent egress).

---

## How I Identified the Real Cause

I ran a one-shot Python diagnostic from my laptop to bypass Kubernetes entirely:

```bash
set -a && source .env.deploy && set +a
python3 /tmp/sf_check.py
```

where `/tmp/sf_check.py` was:

```python
import snowflake.connector, os
c = snowflake.connector.connect(
    account='qztxwkd-lsc26305',
    user='PIPELINE_USER',
    password=os.environ['SNOWFLAKE_PASSWORD'],
    role='PIPELINE_ROLE', warehouse='PIPELINE_WH')
print(c.cursor().execute('SELECT CURRENT_ACCOUNT(), CURRENT_USER()').fetchone())
```

The error was the breakthrough:

> `snowflake.connector.errors.DatabaseError: 250001 (08001): Failed to connect to DB: qztxwkd-lsc26305.snowflakecomputing.com:443. Multi-factor authentication is required for this account. Log in to Snowsight to enroll.`

Three things that error message tells me at once:

1. **The hostname resolves and answers.** It's not a network problem; Snowflake's auth server actively returned this rejection.
2. **The account, locator, role, warehouse, and password are all still valid.** The locator wasn't dead — Snowflake reached `PIPELINE_USER`'s record.
3. **The blocker is an authentication policy, not a credential.** Snowflake recently rolled out a mandate that password-based logins must complete a second factor. My service account has no phone to tap "approve" on, so every login fails.

That third point is the actual root cause.

---

## The Real Root Cause

Snowflake rolled out a global MFA mandate for password-based logins in early 2026. The default account-level authentication policy now requires MFA enrollment and a second factor on every login. This was meant to protect human accounts from password-leak attacks — and it does that well for humans.

But it breaks **service accounts** by design. A service account is a robot — there's no person sitting at a phone to approve a Duo Push for every Airflow task, every dbt model, every dashboard page load. Under the new default policy, every connection from `PIPELINE_USER` was failing with errno 250001, wrapped in a message that mentioned MFA enrollment.

The dashboard's error classifier at `dashboard/db.py:78-100` happens to map errno 250001 to `network_error`, which is correct for the typical case (DNS/host resolution) but wrong here. The classifier did its job — Snowflake just bundles auth-policy errors and connection errors under the same errno.

This affected every component using the `snowflake-credentials` K8s secret:
- The Flask + Dash dashboard
- `dag_stocks_consumer` (Kafka → Snowflake RAW)
- `dag_weather_consumer`
- `anomaly_detector.py`
- All dbt runs

---

## Fix

Three steps. Steps 1 and 3 are the actual unblocker; step 2 is good security hygiene I should do anyway.

### Step 1: Enable MFA on my human Snowflake login (~5 min)

My personal Snowflake login has `ACCOUNTADMIN` rights — adding MFA to it is the right thing to do, and it's exactly what Snowflake's mandate is trying to encourage.

In Snowsight:
1. Click my **avatar in the bottom-left corner**.
2. Click **Profile**.
3. Scroll to **Multi-Factor Authentication**.
4. Click **Enroll**.
5. Open Duo Mobile (free, App Store / Play Store) → tap **+** → scan the QR code Snowsight shows.
6. Type the 6-digit code Duo gives me back into Snowsight.
7. **Save the backup codes Snowsight shows me into a password manager** — one-time codes, only way to recover if I lose my phone.
8. Log out, log back in, tap **Approve** on the Duo prompt that appears on my phone — confirms it works.

This has zero impact on the pipeline because the pipeline does NOT use my human login.

### Step 2: Exempt `PIPELINE_USER` from MFA via an authentication policy (~5 min — this is the unblocker)

Snowflake supports per-user "authentication policies." I created one that says "password-only, no MFA, no enrollment required" and attached it to `PIPELINE_USER` only. Every other user (including my human login) keeps the default account-level MFA policy.

In a Snowsight worksheet, with the role selector at the top set to **ACCOUNTADMIN**, I ran:

```sql
-- Make PIPELINE_USER not require multi-factor authentication because it runs automatically as a bot.
USE ROLE ACCOUNTADMIN;
USE DATABASE PIPELINE_DB;
USE SCHEMA PUBLIC;

CREATE OR REPLACE AUTHENTICATION POLICY PIPELINE_SERVICE_ACCOUNT_POLICY
    AUTHENTICATION_METHODS = ('PASSWORD')
    MFA_ENROLLMENT         = OPTIONAL
    COMMENT = 'Service-account login policy: password-only, MFA-exempt. Used by PIPELINE_USER only.';

ALTER USER PIPELINE_USER
    SET AUTHENTICATION POLICY PIPELINE_DB.PUBLIC.PIPELINE_SERVICE_ACCOUNT_POLICY;

SHOW AUTHENTICATION POLICIES IN SCHEMA PIPELINE_DB.PUBLIC;
```

A few syntax notes (things I tripped over while running this):
- `MFA_AUTHENTICATION_METHODS` is **not a valid property** on this account's edition — Snowflake errors with `invalid property 'MFA_AUTHENTICATION_METHODS' for 'AUTHENTICATION_POLICY'`. Just `AUTHENTICATION_METHODS = ('PASSWORD')` plus `MFA_ENROLLMENT = OPTIONAL` is enough.
- Authentication policies are schema-scoped objects, so the session needs a current database before `CREATE AUTHENTICATION POLICY` will run — hence the `USE DATABASE PIPELINE_DB; USE SCHEMA PUBLIC;` lines. Without them, Snowflake errors with `This session does not have a current database`.
- `ALTER USER ... SET AUTHENTICATION POLICY` does NOT honor `USE DATABASE/SCHEMA`, so the policy must be referenced by its **fully qualified name** (`PIPELINE_DB.PUBLIC.PIPELINE_SERVICE_ACCOUNT_POLICY`).
- The `SHOW AUTHENTICATION POLICIES` query at the end returns a row showing the new policy. (`SHOW PARAMETERS LIKE 'AUTHENTICATION_POLICY'` does NOT — `AUTHENTICATION_POLICY` is a user property, not a parameter, so that query returns 0 rows even when the policy is correctly attached.)

I also appended this same SQL to the **end** of `scripts/snowflake_setup.sql` as section 11, so the next time `--snowflake-setup` runs (e.g. after a credential rotation, or during disaster recovery on a fresh account), the policy is reapplied automatically. The script now creates everything `PIPELINE_USER` needs to log in, including its MFA exemption — fully reproducible.

### Step 3: Redeploy

```bash
cd /Users/David/Documents/Programming/Python/Data-Pipeline-2026/data_pipeline
./scripts/deploy.sh
```

`scripts/deploy/sync.sh:42-89` re-applied the secret and rolled out the dashboard + Airflow pods. Within a couple of minutes, the dashboard switched from "Can't reach Snowflake servers" to real charts. Because the account had been the same all along, my MARTS data was still intact — no DAG re-runs needed.

---

## Verification

1. **Laptop diagnostic** (`python3 /tmp/sf_check.py`) returned `('ADC94167', 'PIPELINE_USER')` — confirms password login now succeeds without an MFA prompt.

2. **Snowsight `SHOW PARAMETERS LIKE 'AUTHENTICATION_POLICY' IN USER PIPELINE_USER;`** returned `PIPELINE_SERVICE_ACCOUNT_POLICY` — confirms the policy is attached to the right user.

3. **Snowsight Profile page** showed MFA enrolled for my human login — confirms humans are still protected.

4. **Dashboard** rendered real Plotly charts on all panels after redeploy — confirms end-to-end recovery.

5. **Airflow UI** — manually triggered one DAG run; it completed without auth errors, confirming Airflow → Snowflake works.

---

## Deferred Work

The auth-policy exemption is officially supported by Snowflake but it's not best practice. The production-grade pattern for service accounts is **RSA key-pair authentication**: a private cryptographic key signs each login, no password is involved at all, and key-pair auth is exempt from MFA by design (because it doesn't use passwords). This is what mature data engineering teams use.

I've added a tracking entry in `docs/TODO.md` to migrate `PIPELINE_USER` to key-pair auth when I have time. Rough scope:

1. Generate a 2048-bit RSA keypair locally with `openssl`.
2. As ACCOUNTADMIN, run `ALTER USER PIPELINE_USER SET RSA_PUBLIC_KEY='...';`.
3. Update `dashboard/db.py:21-44` to authenticate via `private_key=...` instead of `password=...`.
4. Update `scripts/deploy/sync.sh:42-89` so the Airflow connection JSON uses `private_key_file`.
5. Replace `SNOWFLAKE_PASSWORD` in `airflow/manifests/snowflake-secret.yaml` with `SNOWFLAKE_PRIVATE_KEY`.
6. Drop the `PIPELINE_SERVICE_ACCOUNT_POLICY` exemption — no longer needed.

That work is non-urgent and is a good portfolio-piece task. It's not in scope for this incident.

---

## Lessons / Notes

- **The dashboard error message was a red herring** — but only because errno 250001 has two very different causes (DNS failure vs. authentication policy rejection). Always read the *full* exception text from Snowflake, not just the numeric errno or the classifier label. The fix here came directly from the line "Multi-factor authentication is required for this account."

- **A 60-second laptop test beats hours of speculation.** I had been about to rebuild the entire Snowflake account. Running one Python script with the existing credentials told me in seconds that the account was fine and identified the actual root cause. Rule for next time: **before rebuilding anything, run a direct connection test from outside the failing system.**

- **Service accounts and human accounts need different security models.** A robot account can't tap "approve" on a phone. Trying to apply a one-size-fits-all human-grade auth policy to robots will break every automated workflow. Snowflake's authentication policies (or key-pair auth) are how you handle this cleanly. The mental model that helped me: *"PIPELINE_USER is a robot. MFA is for humans. Service accounts need a different proof of identity."*

- **The error classifier in `dashboard/db.py:78-100` did its job.** It surfaced errno 250001 as `network_error`, which is the right label for the typical case. The fact that this specific instance was an auth-policy issue dressed up in the same errno is a Snowflake-side classification quirk, not a dashboard bug. I considered adding a "looks like MFA" branch to the classifier, but the real fix is to never see errno 250001 from `PIPELINE_USER` again — which the auth policy guarantees.

- **`scripts/snowflake_setup.sql` is the disaster-recovery script for the entire Snowflake side.** Now that section 11 captures the auth-policy exemption, a future me running `--snowflake-setup` against a fresh account will get a fully working pipeline — no manual policy attachment needed.

- **If this happens again** (or to anyone else hitting Snowflake's MFA mandate on a service account): do the laptop diagnostic first; if you see "Multi-factor authentication is required," apply Step 2's SQL block and you're done. Don't go down the rebuild-from-scratch rabbit hole I almost went down.
