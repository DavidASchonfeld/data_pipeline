import boto3
import json
import os

from botocore.exceptions import ClientError


def handler(event, context):
    """Re-associates the pipeline EIP to a newly launched ASG instance.

    Triggered by SNS → ASG lifecycle hook on EC2_INSTANCE_LAUNCHING.
    Runs before the instance is marked InService, so SSH/dashboard access
    is available on the same static IP immediately after boot.

    Spot replacement mode (when spot_preempt.tf is deployed and a 2-minute warning fired):
    If SSM flag /pipeline/spot-replacing is "true" AND the EIP is already attached to a
    running instance, defers EIP reassociation. The old instance keeps its EIP so users
    stay connected for the full 2-minute warning window. The spot_restored Lambda moves
    the EIP once the old instance actually terminates.

    Self-detecting: reads the SSM flag at runtime, so no code changes are needed when
    adding or removing spot_preempt.tf — if the flag/parameter is absent it falls through
    to the normal (immediate) reassociation path.
    """
    ec2        = boto3.client("ec2")
    ssm        = boto3.client("ssm")
    asg_client = boto3.client("autoscaling")

    # SNS wraps the ASG lifecycle message as a JSON string inside the Records array
    message = json.loads(event["Records"][0]["Sns"]["Message"])

    # ignore termination hooks — only act on launch
    if message.get("LifecycleTransition") != "autoscaling:EC2_INSTANCE_LAUNCHING":
        print(f"Ignoring non-launch event: {message.get('LifecycleTransition')}")
        return

    instance_id = message["EC2InstanceId"]
    print(f"Launch lifecycle hook triggered for instance {instance_id}")

    # Check SSM for the spot-replacing flag written by spot_preempt Lambda.
    # Any error (ParameterNotFound = flag doesn't exist, AccessDenied = policy removed) is treated
    # as "not a spot replacement" so this Lambda stays correct without any code changes.
    spot_replacing = False
    try:
        ssm_flag_name = os.environ.get("SSM_SPOT_REPLACING", "/pipeline/spot-replacing")
        flag_value = ssm.get_parameter(Name=ssm_flag_name)["Parameter"]["Value"]
        spot_replacing = flag_value == "true"
    except Exception as e:
        # ParameterNotFound: spot_preempt.tf not deployed — normal wake path
        # AccessDeniedException: SSM policy removed alongside spot_preempt.tf — also normal path
        print(f"SSM spot-replacing check skipped ({type(e).__name__}) — treating as normal wake")

    if spot_replacing:
        # Check whether the EIP is currently attached to another running instance
        eip_info        = ec2.describe_addresses(AllocationIds=[os.environ["EIP_ALLOCATION_ID"]])
        eip_association = eip_info["Addresses"][0].get("AssociationId")

        if eip_association:
            # EIP belongs to the still-running old instance — defer the move so users stay connected
            # Store this new instance's ID so spot_restored Lambda knows where to send the EIP
            ssm_new_id_name = os.environ.get("SSM_NEW_INSTANCE_ID", "/pipeline/spot-new-instance-id")
            ssm.put_parameter(Name=ssm_new_id_name, Value=instance_id, Overwrite=True)
            print(f"Spot replacement in progress — deferring EIP; stored replacement instance {instance_id} in SSM")
            # Complete lifecycle hook so the new instance enters InService (without EIP for now)
            asg_client.complete_lifecycle_action(
                LifecycleHookName=message["LifecycleHookName"],
                AutoScalingGroupName=message["AutoScalingGroupName"],
                LifecycleActionToken=message["LifecycleActionToken"],
                LifecycleActionResult="CONTINUE",
            )
            return
        # EIP is not attached to anyone (edge case: old instance already gone) — fall through to normal reassociation

    print(f"Associating EIP to instance {instance_id}")

    # bind the static EIP to the new instance (AllowReassociation handles the case
    # where the EIP is still associated with a terminating instance)
    ec2.associate_address(
        InstanceId=instance_id,
        AllocationId=os.environ["EIP_ALLOCATION_ID"],
        AllowReassociation=True,
    )

    # signal the lifecycle hook so ASG proceeds to mark the instance InService
    asg_client.complete_lifecycle_action(
        LifecycleHookName=message["LifecycleHookName"],
        AutoScalingGroupName=message["AutoScalingGroupName"],
        LifecycleActionToken=message["LifecycleActionToken"],
        LifecycleActionResult="CONTINUE",
    )

    print(f"EIP associated and lifecycle hook completed for {instance_id}")
