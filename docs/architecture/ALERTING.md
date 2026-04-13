# Alerting

How the pipeline notifies you when things break or data goes stale.

---

## How It Works

When a task fails, the pipeline automatically calls a function (`on_failure_alert`) that:
1. Writes the failure to the PVC log file (always happens)
2. Sends a message to Slack (if a webhook URL is configured)

A separate monitoring DAG (`Data_Staleness_Monitor`) runs every 30 minutes. It checks how old the latest data is in each table and alerts if data hasn't been updated in too long.

## Preventing Notification Spam (Cooldown)

Without protection, a single broken task could send 12+ Slack messages per hour. The cooldown system works like a "don't call me again for an hour" rule:

- First time a task fails → you get a Slack message immediately
- Same task fails again within 60 minutes → suppressed (logged but not sent)
- Task finally succeeds → you get one "Task Recovered" message, clock resets

The same rule applies to retries and staleness checks. One alert per issue, not one per occurrence.

The cooldown timer is saved as an Airflow Variable (same system as vacation mode). You can see these in the Airflow UI under Admin → Variables. To be notified again immediately, delete the relevant variable.

## Slack Setup

Slack is a messaging app (like iMessage or WhatsApp but for teams). Alerts appear as regular Slack notifications — they are not sent via email.

A **webhook** is a secret URL that Slack provides. When the pipeline sends a request to that URL, Slack delivers the message to a channel:

```
Pipeline task fails → Python POSTs to https://hooks.slack.com/services/... → Slack shows message
```

Slack is optional. Without it, the alerting system runs in **log-only mode**: failures are still logged to PVC files on EC2, you just won't get push notifications.

To enable Slack notifications, set `SLACK_WEBHOOK_URL` in your K8s secrets and configure a free Slack workspace + webhook URL. See the [alerting runbook](../operations/runbooks/vacation-and-alerting.md) for step-by-step setup.

> **Current status:** The alerting infrastructure is fully built but not connected to a Slack workspace. Running in log-only mode.

## Vacation Mode and Alerts

- **Failure/retry alerts still fire** during vacation — if a DAG fails instead of cleanly skipping, vacation mode itself is broken, which is worth knowing
- **Staleness alerts are silenced** — stale data is expected when pipelines are intentionally paused

## Files

| File | What it does |
|------|-------------|
| `airflow/dags/alerting/` | Alert package — Slack, PVC logging, staleness checking, cooldown |
| `airflow/dags/shared/config.py` | Central config: webhook URL, staleness thresholds |
| `airflow/dags/dag_staleness_check.py` | Monitoring DAG that runs every 30 minutes |
