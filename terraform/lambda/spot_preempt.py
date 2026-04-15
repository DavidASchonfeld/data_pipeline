"""
Proactive spot-interruption replacement Lambda.

Triggered by EventBridge when AWS issues a 2-minute EC2 Spot Instance Interruption Warning.
Scales the ASG to desired=2 immediately so a replacement instance starts booting during
the warning window — cutting the user-visible loading screen from ~3-5 min to ~1-3 min.

Graceful degradation:
- If the ASG does not exist (sleep/wake removed): logs a warning, exits cleanly.
- If SSM put fails: logs a warning, still attempts the ASG scale (partial success is better than none).
Removing terraform/spot_preempt.tf removes this Lambda and its EventBridge rule entirely.
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Scale ASG to desired=2 when AWS issues a spot interruption notice."""
    asg_name           = os.environ["ASG_NAME"]
    ssm_spot_replacing = os.environ["SSM_SPOT_REPLACING"]

    # Extract the interrupted instance ID from the EventBridge payload
    instance_id = event.get("detail", {}).get("instance-id", "unknown")
    logger.info("Spot interruption notice received for instance %s — launching proactive replacement", instance_id)

    # Set the SSM flag so the EIP Lambda defers EIP reassociation to the post-termination Lambda
    try:
        ssm = boto3.client("ssm")
        ssm.put_parameter(Name=ssm_spot_replacing, Value="true", Overwrite=True)
        logger.info("SSM flag %s = true — EIP Lambda will defer reassociation", ssm_spot_replacing)
    except Exception:
        # Non-fatal: the EIP Lambda will fall through to normal reassociation if the flag is missing
        logger.exception("Could not set SSM spot-replacing flag — replacement will still launch but EIP deferred mode is disabled")

    # Scale ASG to 2 so a replacement instance begins booting during the 2-minute warning window
    try:
        asg = boto3.client("autoscaling")
        # Temporarily raise max_size to 2 so ASG allows two simultaneous instances
        asg.update_auto_scaling_group(AutoScalingGroupName=asg_name, MaxSize=2)
        asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=2)
        logger.info("ASG %s scaled to max=2 desired=2 — replacement instance is now launching", asg_name)
    except ClientError as e:
        # ValidationError / ResourceNotFound: ASG name doesn't exist (e.g. sleep/wake Terraform removed)
        logger.warning(
            "ASG scale-up failed [%s] — proactive replacement skipped (no ASG?): %s",
            e.response["Error"]["Code"], e.response["Error"]["Message"],
        )
    except Exception:
        logger.exception("Unexpected error scaling ASG — proactive replacement skipped")
