# Multi-Client Access — Verification Guide

**Short answer: yes.** Two or more computers can access the dashboard at the same time, staggered times, or completely different times. This document explains why and walks through tests to prove it.

> **Note (2026-04-15):** The sleep/wake system has been removed. The server now runs continuously. Tests T1, T2, and T5 (which required a sleeping server or idle-timer manipulation) are no longer applicable. Tests T3 and T4 remain fully valid and cover the important multi-client scenarios.

---

## Why It Works

| Mechanism | What it does | Why it handles multiple clients |
|---|---|---|
| Always-on ASG (min=1) | One spot instance is always running | No wake-up wait — every visitor gets the live dashboard immediately |
| HTTP direct to EIP | Visitors connect directly to the server's static IP | Stateless — every caller independently fetches their page, no interference |
| Flask concurrent sessions | Flask handles multiple HTTP requests simultaneously | Per-request state — sessions are independent with no shared mutable state |

---

## Quick Checklist

| # | Scenario | Pass Condition |
|---|---|---|
| T3 | Client B arrives while server is already running | Immediate response — no loading page |
| T4 | Both clients browse the live dashboard at the same time | Both work normally with no interference |

> Tests T1, T2, and T5 required a sleeping server and are no longer applicable.

---

## Prerequisites

```bash
export AWS_PROFILE=terraform-dev
export AWS_REGION=us-east-1

EIP="52.70.211.1"
PORT="32147"
APIGW="https://im6g5ue81k.execute-api.us-east-1.amazonaws.com"
```

---

## Test T1 — Simultaneous Access (Server Sleeping)

Two clients hit the API GW at the same time while the server is asleep.

**Setup — ensure the server is sleeping:**

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].DesiredCapacity"
# Expected: 0
```

If desired is not 0, use the sleep procedure in VERIFY_SLEEP_WAKE_SPOT.md step 4b to put it to sleep first.

**Simulate two simultaneous requests:**

```bash
# Run both in the same terminal line so they fire nearly simultaneously
curl -s -o /tmp/client_a.html "$APIGW/dashboard/" &
curl -s -o /tmp/client_b.html "$APIGW/dashboard/" &
wait
```

**Expected — loading page served to both:**

```bash
grep -o '<title>[^<]*</title>' /tmp/client_a.html
grep -o '<title>[^<]*</title>' /tmp/client_b.html
# Both: <title>Data Pipeline Dashboard</title>
```

**Expected — only one instance launched:**

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].{Desired:DesiredCapacity,InstanceCount:length(Instances)}"
# Expected: Desired=1, InstanceCount=1 (not 2)
```

**Expected — SSM timestamp was updated (both writers, one value):**

```bash
aws ssm get-parameter --name /pipeline/last-activity-timestamp \
  --query "Parameter.Value" --output text
# Expected: a recent Unix timestamp (within a few seconds of now)
```

---

## Test T2 — Staggered Access (Client B Arrives Mid-Boot)

Client A triggers the wake. While the instance is still booting (ASG desired=1, no InService instance yet), Client B arrives.

**Setup — trigger a wake from Client A:**

```bash
curl -s -o /dev/null "$APIGW/dashboard/"
# Server starts booting. It won't be InService yet for ~30-60 seconds.
```

**Immediately (within ~30s) make a request from Client B:**

```bash
curl -s -o /tmp/client_b_staggered.html "$APIGW/dashboard/"
```

**Expected — Client B gets a loading page with "Server is booting up..." message:**

```bash
grep 'Server is booting up' /tmp/client_b_staggered.html
# Expected: one matching line
```

The estimated time shown to Client B will be 180 seconds (booting state), different from Client A's 240-second estimate (from-sleep state). Each browser's `sessionStorage` preserves its own start time and estimate independently, so the countdowns shown in each tab are not synchronized.

**Expected — still only one instance:**

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].{Desired:DesiredCapacity,InstanceCount:length(Instances)}"
# Expected: Desired=1, InstanceCount=1
```

---

## Test T3 — New Visitor While Server Is Already Running

The server is already healthy. A new client accesses the API GW and should be redirected instantly with no loading page.

**Setup — confirm server is up:**

```bash
curl -s "http://$EIP:$PORT/health"
# Expected: {"status":"ok"}
```

**Make a new request:**

```bash
curl -I "$APIGW/dashboard/"
```

**Expected: immediate HTTP 302, no loading page:**

```
HTTP/2 302
location: http://52.70.211.1:32147/dashboard/
```

The `location:` header should be present and point to the EIP. This confirms that new clients joining an already-running session bypass the loading page entirely.

---

## Test T4 — Concurrent Active Sessions

Both clients are browsing the live dashboard at the same time.

**Open the dashboard in two separate browser tabs or two different computers.** Navigate to different pages in each (e.g., `/dashboard/` in one and `/weather/` in the other).

**Expected:** both tabs load and respond normally with no errors. Flask handles concurrent HTTP sessions independently — there is no per-session state that would cause interference.

**Confirm the server health endpoint still responds normally under concurrent load:**

```bash
# Fire five requests in parallel
for i in 1 2 3 4 5; do
    curl -s "http://$EIP:$PORT/health" &
done
wait
# Expected: five lines of {"status":"ok"}
```

---

## Test T5 — Idle Timer Reset by a Second Client

Client A visited 40 minutes ago. The server would sleep in ~5 minutes if no one else visits. Client B arrives now and resets the clock.

**Setup — simulate Client A's last visit 40 minutes ago:**

```bash
FORTY_MIN_AGO=$(date -u -v-40M +%s)   # macOS
# FORTY_MIN_AGO=$(date -u -d '40 minutes ago' +%s)   # Linux

aws ssm put-parameter \
  --name /pipeline/last-activity-timestamp \
  --value "$FORTY_MIN_AGO" --overwrite
```

**Client B visits:**

```bash
curl -s -o /dev/null "$APIGW/dashboard/"
```

**Confirm the timestamp was updated to now:**

```bash
NOW=$(date +%s)
LAST=$(aws ssm get-parameter --name /pipeline/last-activity-timestamp \
  --query "Parameter.Value" --output text)
echo "Seconds ago: $((NOW - LAST))"
# Expected: a small number (< 10 seconds)
```

**Invoke the sleep Lambda manually — it should not scale down:**

```bash
aws lambda invoke \
  --function-name pipeline-sleep \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/sleep-t5.json
aws logs tail /aws/lambda/pipeline-sleep --since 2m
```

**Expected log:** `Active: last activity Xs ago, sleeping in Ys` — no mention of scaling. The server stays alive because Client B's visit refreshed the idle clock.
