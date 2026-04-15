"""Sleep Lambda — shuts down the pipeline after a period of inactivity.

Triggered every 15 minutes by EventBridge. Checks how long it has been since
the last visitor or deploy activity, and scales the ASG to 0 if the idle
timeout has been exceeded. This saves money by only running the instance
when someone is actually using it.
"""

import os
import time

import boto3


# AWS clients — created once per Lambda cold start to reuse connections
asg_client = boto3.client("autoscaling")
ssm_client = boto3.client("ssm")

# Environment variables set by Terraform
ASG_NAME = os.environ["ASG_NAME"]
IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "45"))
SSM_LAST_ACTIVITY = os.environ["SSM_LAST_ACTIVITY"]
SSM_DEPLOY_ACTIVE = os.environ["SSM_DEPLOY_ACTIVE"]


def _get_ssm_value(name, default="0"):
    """Read a value from SSM Parameter Store, returning a default if it does not exist."""
    try:
        resp = ssm_client.get_parameter(Name=name)
        return resp["Parameter"]["Value"]
    except ssm_client.exceptions.ParameterNotFound:
        return default


def handler(event, context):
    """Main entry point — EventBridge calls this every 15 minutes to check for idle instances."""

    # Skip shutdown if a deploy is currently running (prevents killing the instance mid-deploy)
    deploy_active = _get_ssm_value(SSM_DEPLOY_ACTIVE, default="false")
    if deploy_active.lower() == "true":
        print("Deploy is in progress — skipping sleep check")
        return

    # Check the ASG — if it is already at 0, there is nothing to shut down
    asg_resp = asg_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[ASG_NAME]
    )
    desired = asg_resp["AutoScalingGroups"][0]["DesiredCapacity"]
    if desired == 0:
        print("Already sleeping (desired=0) — nothing to do")
        return

    # Calculate how long the instance has been idle since the last visitor or deploy
    last_activity = int(_get_ssm_value(SSM_LAST_ACTIVITY, default="0"))
    idle_seconds = int(time.time()) - last_activity
    idle_timeout_seconds = IDLE_TIMEOUT_MINUTES * 60

    # If someone visited or deployed recently, keep the instance running
    if idle_seconds < idle_timeout_seconds:
        remaining = idle_timeout_seconds - idle_seconds
        print(
            f"Active: last activity {idle_seconds}s ago, "
            f"sleeping in {remaining}s"
        )
        return

    # Idle timeout exceeded — shut down the instance to save money
    print(
        f"Idle for {idle_seconds}s (timeout: {idle_timeout_seconds}s) — "
        f"scaling ASG to 0"
    )
    asg_client.set_desired_capacity(
        AutoScalingGroupName=ASG_NAME,
        DesiredCapacity=0,
    )
    print("ASG scaled to 0 — instance will terminate shortly")
