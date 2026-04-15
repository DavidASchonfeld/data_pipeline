# Spot Interruption & Server Health — Verification Guide

> **Note (2026-04-15):** The sleep/wake system has been removed. The server now runs continuously on spot pricing. Steps 1a–1d, Step 2, and Step 4 (sleep/wake cycle) in this guide are no longer applicable and are kept here for historical reference only. Steps 3, 5, 6, and 7 remain valid and describe how to verify the always-on server and spot interruption handling.

This document walks through verifying that the pipeline's availability and spot recovery features are working correctly. It covers:

- **Spot interruption handling:** if AWS reclaims the server with a 2-minute warning, the pipeline boots a replacement and moves the public IP over automatically.
- **AMI bake:** a disk snapshot of the fully configured server is kept up to date so new instances boot in 3–5 minutes instead of 20+.
- **Server bootstrap health:** confirms swap, AWS CLI, and Kubernetes are all present and healthy.

Run these checks in order. Each section lists what to run, what to expect, and what a failure means.

---

## Quick Checklist

| # | What | Pass Condition |
|---|---|---|
| 1b | Auto Scaling Group exists | min=1, max=2, desired=1 (always-on) |
| 1d | 2 EventBridge rules exist | pipeline-spot-interruption / pipeline-ec2-terminated |
| 3a | /health returns OK | `{"status":"ok"}` |
| 3b | /api/spot-status returns OK | `{"interruption":false}` |
| 3c | /validation returns data | Row counts for both financials and weather |
| 5 | AMI snapshot is current | State=available, launch template points to it |
| 6a | Swap file active | `swapon --show` shows 4G |
| 6b | AWS CLI version | `aws --version` shows 2.x.x |
| 6c | Kubernetes node ready | `kubectl get nodes` shows STATUS=Ready |

> Steps 1a, 1c, 2, and 4 from the original guide (wake/sleep Lambda, API Gateway, SSM activity flags, sleep→wake cycle) are no longer applicable. The pipeline-wake and pipeline-sleep Lambdas have been removed along with the API Gateway and the idle-timer SSM parameters.

---

## Prerequisites

Before running any checks, set these variables in your terminal:

```bash
# AWS profile and region
export AWS_PROFILE=terraform-dev
export AWS_REGION=us-east-1

# Server public IP and port
EIP="52.70.211.1"
PORT="32147"

# API Gateway base URL
APIGW="https://im6g5ue81k.execute-api.us-east-1.amazonaws.com"

# Dashboard admin password (from .env.deploy)
VALIDATION_PASS="<value of VALIDATION_PASS from .env.deploy>"
```

---

## Step 1 — AWS Infrastructure

Run all four commands. Each should complete with no errors.

### 1a. Lambda functions

```bash
aws lambda list-functions \
  --query "Functions[?starts_with(FunctionName,'pipeline-')].FunctionName" \
  --output json
```

**Expected:** exactly these five names:
```json
["pipeline-eip-reassociate", "pipeline-sleep", "pipeline-spot-preempt", "pipeline-spot-restored", "pipeline-wake"]
```

---

### 1b. Auto Scaling Group

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity}"
```

**Expected:** `Min=1, Max=2, Desired=1` — the server is always-on. Max=2 allows a temporary second instance during a spot replacement.

---

### 1c. SSM configuration values

These are the runtime flags the Lambdas use to coordinate with each other.

```bash
aws ssm get-parameters \
  --names /pipeline/last-activity-timestamp \
          /pipeline/deploy-active \
          /pipeline/spot-replacing \
          /pipeline/spot-new-instance-id \
  --query "Parameters[*].[Name,Value]" --output table
```

**Expected:** all four parameters are returned (no `InvalidParameters` list in the response). Typical values while running normally:

| Parameter | Normal value |
|---|---|
| last-activity-timestamp | Recent Unix timestamp (epoch seconds) |
| deploy-active | `false` |
| spot-replacing | `false` |
| spot-new-instance-id | `none` |

---

### 1d. EventBridge rules

```bash
aws events list-rules \
  --query "Rules[?starts_with(Name,'pipeline-')].Name" --output json
```

**Expected:**
```json
["pipeline-ec2-terminated", "pipeline-spot-interruption"]
```

- `pipeline-spot-interruption` — fires when AWS sends a spot termination warning
- `pipeline-ec2-terminated` — fires when an instance finishes terminating (used to move the public IP to the replacement)

> `pipeline-sleep-check` has been removed — the server no longer sleeps.

---

## Step 2 — API Gateway Redirects

The API Gateway is the public entry point. When the server is awake, it redirects visitors to the actual dashboard. When the server is asleep, it wakes it up and shows a loading page instead.

```bash
# Test the main dashboard route
curl -I "$APIGW/dashboard/"

# Test the weather dashboard route
curl -I "$APIGW/weather/"
```

**Expected for both:** `HTTP/2 302` with a `location:` header pointing to `http://52.70.211.1:32147/<path>/`

If you get `HTTP/2 200` instead, the server is asleep and the loading page is being served (also correct — it means the server is starting up).

---

## Step 3 — Dashboard Endpoints

These hit the server directly to confirm it is running and serving data correctly.

### 3a. Health check

```bash
curl -s "http://$EIP:$PORT/health"
```

**Expected:** `{"status":"ok"}`

---

### 3b. Spot interruption status

```bash
curl -s "http://$EIP:$PORT/api/spot-status"
```

**Expected:** `{"interruption":false,"termination_time":null}`

If `interruption` is `true`, an AWS spot termination warning is active and the replacement boot sequence is underway — this is not an error, just a live event.

---

### 3c. Data validation

```bash
curl -s -u "admin:$VALIDATION_PASS" "http://$EIP:$PORT/validation" | python3 -m json.tool
```

**Expected:** a JSON object where `status` is `"ok"` and `tables` contains both `company_financials` and `weather_hourly` with non-zero `row_count` values.

---

## Step 4 — Sleep / Wake Cycle

### 4a. Safe invoke (no downtime)

This invokes the sleep Lambda manually. Since the server has been active recently, it should detect that the idle timeout has not been reached and do nothing.

```bash
aws lambda invoke \
  --function-name pipeline-sleep \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/sleep-resp.json
cat /tmp/sleep-resp.json

aws logs tail /aws/lambda/pipeline-sleep --since 5m
```

**Expected:** the log shows something like `Active: last activity Xs ago, sleeping in Ys` — no mention of scaling or shutting down.

---

### 4b. Full sleep→wake cycle

> **Warning:** this shuts the server down temporarily (typically 3–5 minutes with a baked AMI). The loading page will be shown during that window.

**Step 1 — Back-date the idle timer:**

```bash
# Set the timestamp to 2 minutes ago so the Lambda thinks the server is idle
BACKDATED=$(date -u -v-2M +%s)   # macOS
# BACKDATED=$(date -u -d '2 minutes ago' +%s)   # Linux
aws ssm put-parameter \
  --name /pipeline/last-activity-timestamp \
  --value "$BACKDATED" --overwrite
```

**Step 2 — Lower the idle timeout to 1 minute:**

```bash
aws lambda update-function-configuration \
  --function-name pipeline-sleep \
  --environment "Variables={IDLE_TIMEOUT_MINUTES=1,ASG_NAME=pipeline-asg,SSM_LAST_ACTIVITY=/pipeline/last-activity-timestamp,SSM_DEPLOY_ACTIVE=/pipeline/deploy-active}"
```

**Step 3 — Invoke the sleep Lambda:**

```bash
aws lambda invoke \
  --function-name pipeline-sleep \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/sleep-resp2.json
aws logs tail /aws/lambda/pipeline-sleep --since 2m
```

**Expected log:** `Idle for Xs (timeout: 60s) — scaling ASG to 0` followed by `ASG scaled to 0 — instance will terminate shortly`

**Step 4 — Restore the idle timeout immediately:**

```bash
aws lambda update-function-configuration \
  --function-name pipeline-sleep \
  --environment "Variables={IDLE_TIMEOUT_MINUTES=45,ASG_NAME=pipeline-asg,SSM_LAST_ACTIVITY=/pipeline/last-activity-timestamp,SSM_DEPLOY_ACTIVE=/pipeline/deploy-active}"
```

**Step 5 — Confirm ASG scaled to 0:**

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Instances:Instances[*].{Id:InstanceId,State:LifecycleState}}"
```

**Expected:** `Desired=0`, instance in `Terminating` state.

**Step 6 — Hit the API Gateway to trigger a wake:**

```bash
curl -sL "$APIGW/dashboard/" | grep -o '<title>[^<]*</title>'
```

**Expected:** `<title>Data Pipeline Dashboard</title>` (the loading page title, served while the new instance boots).

Behind the scenes, hitting the API GW triggers the wake Lambda, which sets ASG desired back to 1.

**Step 7 — Wait for the server to come back up:**

```bash
# Poll until /health responds — typically 3–5 minutes with a baked AMI
for i in $(seq 1 30); do
    STATUS=$(curl -s --connect-timeout 5 "http://$EIP:$PORT/health" 2>/dev/null)
    if [ "$STATUS" = '{"status":"ok"}' ]; then
        echo "Server is back up (attempt $i)"
        break
    fi
    echo "Not yet up (attempt $i/30)..."
    sleep 10
done
```

**Step 8 — Confirm ASG is at desired=1:**

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Instances:Instances[*].{Id:InstanceId,State:LifecycleState}}"
```

**Expected:** `Desired=1`, instance `InService`.

---

#### K3s state after wake — fixed

This issue was resolved. `scripts/deploy/ami.sh` now force-deletes all pods and the current node from K3s's SQLite database before stopping K3s for the snapshot (step 3b). `terraform/user-data.sh.tpl` was updated to apply the Flask pod manifest on every boot (instead of only waiting for a pod that no longer exists in the clean DB).

If you still see two nodes after a wake (e.g. on an instance booted from an AMI baked before this fix), the manual recovery steps are:

```bash
# SSH to the instance
ssh ec2-stock

# Remove the stale node — evicts any pods pinned to it
kubectl delete node <old-node-name>

# Re-apply the Flask pod with the real ECR registry URL
ECR="683010036255.dkr.ecr.us-east-1.amazonaws.com"
sed "s|\${ECR_REGISTRY}|$ECR|g" /home/ubuntu/dashboard/manifests/pod-flask.yaml \
  | kubectl apply -n default -f -
```

Then rebake the AMI (`./scripts/deploy.sh --provision`) so future instances use the corrected bake script.

---

## Step 5 — AMI Snapshot Status

The AMI is a saved disk image of the fully configured server. New instances boot from it so they are ready in minutes instead of going through a full setup.

```bash
aws ec2 describe-images --owners self \
  --filters "Name=name,Values=pipeline-*" \
  --query "Images[*].[ImageId,Name,State,CreationDate]" --output table
```

**Expected:** at least one row with `State=available`. The creation date should be recent (within a few days).

**Verify the launch template is using it:**

```bash
# Get the launch template ID from Terraform output or the ASG configuration
LT_ID=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names pipeline-asg \
  --query "AutoScalingGroups[0].MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification.LaunchTemplateId" \
  --output text)

aws ec2 describe-launch-template-versions \
  --launch-template-id "$LT_ID" --versions '$Latest' \
  --query "LaunchTemplateVersions[0].LaunchTemplateData.ImageId" --output text
```

**Expected:** the AMI ID matches the one returned by `describe-images`.

To rebake the AMI (for example after making changes to the server configuration):

```bash
./scripts/deploy.sh --provision --bake-ami
```

---

## Step 6 — Server Bootstrap Checks

SSH to the server and run the following. These confirm that the core dependencies installed during initial setup are still present.

```bash
ssh ec2-stock
```

### 6a. Swap file

```bash
swapon --show
```

**Expected:** one line showing a file of size `4G`.

If empty, swap is missing. This is a safety net against out-of-memory errors during container image builds. To add it:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

Then rebake the AMI so future instances inherit the swap file.

---

### 6b. AWS CLI version

```bash
aws --version
```

**Expected:** `aws-cli/2.x.x ...` (major version 2).

---

### 6c. Kubernetes node status

```bash
kubectl get nodes
```

**Expected:** one node with `STATUS=Ready`. If two nodes appear, the old node from the previous boot is still registered — delete it with `kubectl delete node <old-node-name>`.

---

## Step 7 — Spot Interruption Lambdas

The spot interruption Lambdas (`pipeline-spot-preempt` and `pipeline-spot-restored`) cannot be triggered manually without an actual AWS spot interruption event. Confirming they exist and are wired to the correct EventBridge rules (checked in Step 1d) is sufficient.

To review their logic:

```bash
# View the spot preemption handler code
cat terraform/lambda/spot_preempt.py

# View the restoration handler code
cat terraform/lambda/spot_restored.py
```

What they do:
- **spot-preempt:** receives the 2-minute warning, immediately boots a replacement spot instance, and stores its ID in SSM so the `eip-reassociate` Lambda knows to defer the IP move.
- **spot-restored:** fires when the original instance finishes terminating and moves the public EIP to the replacement instance.

---

## Troubleshooting Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| API GW returns 5xx | Wake Lambda misconfigured | Check `/aws/lambda/pipeline-wake` CloudWatch logs |
| Loading page never resolves | Instance not booting | Check ASG activity history; check `/var/log/pipeline-boot.log` on the instance |
| Dashboard up but stale K3s node | AMI baked with old cluster state | `kubectl delete node <old-node>`, then apply pod manifests |
| Sleep Lambda scales down too early | SSM timestamp stale or Lambda env var wrong | Verify `IDLE_TIMEOUT_MINUTES` env var on `pipeline-sleep` Lambda |
| EIP not moving to new instance | `eip-reassociate` Lambda error | Check CloudWatch logs; if stuck in `Pending:Wait`, the lifecycle hook will auto-continue after 5 minutes (`DefaultResult=CONTINUE`) |
| Swap missing on new instance | Swap not included in baked AMI | Add swap file on instance, then rebake AMI |
