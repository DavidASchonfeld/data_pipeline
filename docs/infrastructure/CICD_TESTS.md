# Automated Tests — Plain English Guide

This document explains what happens automatically whenever code is changed in this project, and how to tell if something went wrong.

---

## What is a CI test?

Think of it like a spell-checker that runs every time you save a document — except instead of checking spelling, it checks that the code still works correctly. These checks run automatically in the cloud (on GitHub) so you don't have to remember to run them yourself.

In this project there are two automated checks:

| Check | What it verifies |
|-------|-----------------|
| **pytest** | The dashboard and data logic produce correct results |
| **dbt tests** | The data tables in Snowflake contain valid, well-formed data |

---

## When do the checks run?

### pytest (dashboard logic)

Runs automatically when:
- You push any code change to GitHub
- You open or update a Pull Request that touches the `dashboard/` folder or the `tests/` folder

It does **not** connect to Snowflake or the live server — it runs entirely inside GitHub's cloud environment with no external dependencies.

### dbt tests (Snowflake data quality)

Runs automatically when:
- You open or update a Pull Request that touches files inside `airflow/dags/dbt/`

> **Note:** This check **does** connect to the live Snowflake account and will use a small amount of compute (a fraction of a cent per run). It does not run on a plain `git push` — only when a Pull Request is open and dbt files have changed.

---

## What exactly is being tested?

### pytest — 6 test files, ~30 checks total

| Test file | What it checks |
|-----------|---------------|
| `test_db.py` | Query results are cached correctly and the cache expires on time |
| `test_chart_utils.py` | Charts and colour maps are built without errors |
| `test_anomaly_table.py` | Anomaly severity labels and table rows are formatted correctly |
| `test_gate_utils.py` | The daily "has new data arrived?" gate returns the right answer |
| `test_security.py` | The dashboard correctly reads the visitor's real IP address |
| `test_weather_anomalies.py` | Weather anomaly calculations produce statistically correct results |

### dbt tests — data quality rules applied to Snowflake tables

These tests verify the data itself, not the code. Examples of what they catch:
- A column that should never be empty suddenly has blank values
- A numeric field contains an unexpected negative number
- A date column has values far outside the expected range
- Duplicate rows appear where only unique rows are expected

---

## How to see whether a check passed or failed

1. Go to the repository on GitHub
2. Click the **Actions** tab at the top
3. Each run is listed with either a green checkmark (passed) or a red X (failed)
4. Click any run to see a detailed log

On a Pull Request, the checks also appear directly at the bottom of the PR page — you will see a green "All checks have passed" banner or a list of failing checks with a "Details" link.

---

## What to do if a check fails

### pytest failure

The log will show which test failed and the exact line of code that caused it. Common causes:
- A function was changed in a way that broke an existing behaviour
- A new feature was added but its output format doesn't match what the rest of the code expects

Fix the code, push again, and the check will re-run automatically.

### dbt test failure

The log will show which table and which rule failed (e.g. "not_null check failed on `FCT_WEATHER_HOURLY.temperature_c`"). Common causes:
- A data pipeline wrote bad or incomplete data to Snowflake
- A new dbt model has a logic error that produces null or duplicate values

Fix the dbt model or the upstream pipeline, push to the branch, and the check re-runs on the next commit to the open PR.

---

## Cost impact

| Check | Connects to Snowflake? | Cost per run |
|-------|----------------------|-------------|
| pytest | No | Free (GitHub-hosted runner) |
| dbt tests | Yes | < $0.01 (a few seconds of XSMALL warehouse compute) |

The dbt warehouse auto-suspends after each run, so there is no idle charge between runs.

---

## Where the configuration lives

| File | What it controls |
|------|-----------------|
| `.github/workflows/test.yml` | When pytest runs and what packages it installs |
| `.github/workflows/dbt-test.yml` | When dbt tests run and which Snowflake credentials to use |
| `tests/` | The individual pytest test files |
| `airflow/dags/dbt/` | The dbt models and the data quality rules attached to them |

Snowflake credentials for the dbt check are stored as encrypted GitHub Secrets (never in the code). To add or rotate them: **GitHub repo → Settings → Secrets and variables → Actions**.
