"""
Post-termination spot-replacement finalization Lambda.

Triggered by EventBridge when any EC2 instance in the account reaches the 'terminated' state.
Checks the SSM flag written by spot_preempt.py to determine whether this termination is part
of a proactive spot replacement. If it is:
  1. Reassociates the static EIP to the new (already-booting) replacement instance.
  2. Resets ASG max=2 / desired=1 back to normal.
  3. Clears both SSM flags so future wakes behave normally.

Graceful degradation:
- If SSM flag is absent or "none": exits immediately — not a spot replacement, ignore.
- If ASG or EIP operations fail: logs the error, still clears SSM flags to avoid getting stuck.
Removing terraform/spot_preempt.tf removes this Lambda and its EventBridge rule entirely.
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Finalize spot replacement: move EIP to new instance and reset ASG after old instance terminates."""
    eip_allocation_id   = os.environ["EIP_ALLOCATION_ID"]
    asg_name            = os.environ["ASG_NAME"]
    ssm_spot_replacing  = os.environ["SSM_SPOT_REPLACING"]
    ssm_new_instance_id = os.environ["SSM_NEW_INSTANCE_ID"]

    terminated_id = event.get("detail", {}).get("instance-id", "unknown")
    logger.info("Instance %s terminated — checking for pending spot replacement", terminated_id)

    # Read the replacement instance ID stored by the EIP Lambda during the proactive launch
    ssm = boto3.client("ssm")
    try:
        new_instance_id = ssm.get_parameter(Name=ssm_new_instance_id)["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            # SSM param doesn't exist — spot_preempt.tf not deployed; nothing to do
            logger.info("SSM parameter %s not found — not a spot replacement scenario", ssm_new_instance_id)
        else:
            logger.exception("SSM read failed — cannot complete spot replacement")
        return

    if not new_instance_id or new_instance_id == "none":
        # Flag exists but no instance ID written — not a spot replacement termination (e.g. normal sleep)
        logger.info("SSM spot-new-instance-id is '%s' — not a spot replacement, nothing to do", new_instance_id)
        return

    logger.info("Spot replacement in progress — moving EIP to replacement instance %s", new_instance_id)

    ec2  = boto3.client("ec2")
    asg  = boto3.client("autoscaling")

    # Reassociate the static EIP to the new instance so it gets the stable public IP
    try:
        ec2.associate_address(
            InstanceId=new_instance_id,
            AllocationId=eip_allocation_id,
            AllowReassociation=True,  # safe even if briefly attached to another instance
        )
        logger.info("EIP reassociated to replacement instance %s", new_instance_id)
    except ClientError:
        logger.exception("EIP reassociation failed — instance may still be in pending state; Wake Lambda will recover on next visit")

    # Explicitly terminate the non-EIP instance before scaling down so the scale-down is deterministic.
    # Without this, ASG's default "oldest instance first" termination policy will pick new_instance_id
    # (it launched first) and keep the other instance, which has no EIP — orphaning it.
    try:
        asg_instances = asg.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )["AutoScalingGroups"][0]["Instances"]
        for inst in asg_instances:
            if inst["InstanceId"] != new_instance_id and inst["LifecycleState"] == "InService":
                ec2.terminate_instances(InstanceIds=[inst["InstanceId"]])
                logger.info("Terminated non-EIP instance %s so EIP holder survives scale-down", inst["InstanceId"])
                break
    except ClientError as e:
        logger.warning("Could not terminate non-EIP instance [%s] — scale-down may orphan EIP", e.response["Error"]["Code"])

    # Reset ASG capacity back to normal now the old spot instance is gone
    try:
        asg.update_auto_scaling_group(AutoScalingGroupName=asg_name, MaxSize=2)  # matches always-on Terraform max_size=2
        asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=1)
        logger.info("ASG %s reset to max=2 desired=1 — normal always-on operation restored", asg_name)
    except ClientError as e:
        logger.warning("ASG reset failed [%s] — manual check may be needed", e.response["Error"]["Code"])
    except Exception:
        logger.exception("Unexpected error resetting ASG capacity")

    # Clear both SSM flags so the next normal wake-from-sleep uses the standard EIP path
    try:
        ssm.put_parameter(Name=ssm_spot_replacing,  Value="false", Overwrite=True)
        ssm.put_parameter(Name=ssm_new_instance_id, Value="none",  Overwrite=True)
        logger.info("SSM flags cleared — spot replacement complete")
    except Exception:
        logger.exception("Failed to clear SSM flags — next wake may incorrectly defer EIP; check SSM manually")
