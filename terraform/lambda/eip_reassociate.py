import boto3
import json
import os


def handler(event, context):
    """Re-associates the pipeline EIP to a newly launched ASG instance.

    Triggered by SNS → ASG lifecycle hook on EC2_INSTANCE_LAUNCHING.
    Runs before the instance is marked InService, so SSH/dashboard access
    is available on the same static IP immediately after boot.
    """
    ec2 = boto3.client("ec2")
    asg_client = boto3.client("autoscaling")

    # SNS wraps the ASG lifecycle message as a JSON string inside the Records array
    message = json.loads(event["Records"][0]["Sns"]["Message"])

    # ignore termination hooks — only act on launch
    if message.get("LifecycleTransition") != "autoscaling:EC2_INSTANCE_LAUNCHING":
        print(f"Ignoring non-launch event: {message.get('LifecycleTransition')}")
        return

    instance_id = message["EC2InstanceId"]
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
