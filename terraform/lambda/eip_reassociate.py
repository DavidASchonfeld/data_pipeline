import boto3
import json
import os
import time

from botocore.exceptions import ClientError

# ASG lifecycle states that mean "this instance is alive and counts toward capacity"
_ACTIVE_STATES = {"Pending", "Pending:Wait", "Pending:Proceed", "InService"}
# ASG errors that just mean "busy right now" — worth a short bounded retry
_RETRYABLE_ASG_ERRORS = {"ScalingActivityInProgress", "ResourceContention"}


def _describe_group(asg_client, asg_name):
    """Returns the ASG description, failing loudly if the group is missing."""
    groups = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name]).get("AutoScalingGroups") or []
    if not groups:
        raise ValueError(f"ASG {asg_name} not found")
    return groups[0]


def _active_asg_instance_ids(asg_client, asg_name):
    """Returns the IDs of ASG instances that are alive (launching or in service) — not terminating."""
    group = _describe_group(asg_client, asg_name)
    return {i["InstanceId"] for i in group.get("Instances", []) if i.get("LifecycleState") in _ACTIVE_STATES}


def _keep_only(asg_client, asg_name, keep_id, attempts=3):
    """Removes every other live ASG instance BY NAME, lowering desired capacity as it goes.

    I don't use set_desired_capacity(1) here: that lets the ASG pick which instance to drop,
    and on Nov 22 2026 it dropped the one holding the EIP. terminate_instance_in_auto_scaling_group
    with ShouldDecrementDesiredCapacity=True removes exactly the named instance, so the EIP holder survives.
    """
    group = _describe_group(asg_client, asg_name)
    live_ids = {i["InstanceId"] for i in group.get("Instances", []) if i.get("LifecycleState") in _ACTIVE_STATES}
    extras = sorted(live_ids - {keep_id})

    # Nothing extra is alive but desired is still >1 (e.g. old instance mid-termination): lower it so the
    # ASG doesn't launch another. Safe here — the EIP holder is the only live instance, so it can't be picked.
    if not extras and group.get("DesiredCapacity", 1) > 1:
        asg_client.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=1)
        print(f"ASG {asg_name} desired capacity set to 1 — {keep_id} is the only live instance")

    for extra_id in extras:
        # Bounded retry: the ASG may still be busy with the launch that triggered this Lambda
        for attempt in range(1, attempts + 1):
            try:
                asg_client.terminate_instance_in_auto_scaling_group(
                    InstanceId=extra_id, ShouldDecrementDesiredCapacity=True,
                )
                print(f"Removed extra instance {extra_id} (desired capacity lowered) so EIP holder {keep_id} survives")
                break
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "Unknown")
                if code in _RETRYABLE_ASG_ERRORS and attempt < attempts:
                    time.sleep(2 * attempt)  # 2s, then 4s — fits well inside the 60s Lambda timeout
                    continue
                raise
    return extras


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
            # Only store the first replacement instance — if two instances launch during a spot event
            # (ASG briefly at desired=2), a second deferral must not overwrite the first so that
            # spot_restored moves the EIP to the surviving instance, not the one ASG will scale down.
            ssm_new_id_name = os.environ.get("SSM_NEW_INSTANCE_ID", "/pipeline/spot-new-instance-id")
            current_target = ssm.get_parameter(Name=ssm_new_id_name)["Parameter"]["Value"]
            if current_target == "none":
                ssm.put_parameter(Name=ssm_new_id_name, Value=instance_id, Overwrite=True)
                print(f"Spot replacement in progress — deferring EIP; stored replacement instance {instance_id} in SSM")
            else:
                print(f"Spot replacement in progress — deferring EIP; replacement already stored ({current_target}), ignoring {instance_id}")
            # Complete lifecycle hook so the new instance enters InService (without EIP for now)
            asg_client.complete_lifecycle_action(
                LifecycleHookName=message["LifecycleHookName"],
                AutoScalingGroupName=message["AutoScalingGroupName"],
                LifecycleActionToken=message["LifecycleActionToken"],
                LifecycleActionResult="CONTINUE",
            )
            return
        # EIP is not attached to anyone (edge case: old spot instance died before the 2-min defer window expired).
        # If a previous launch already deferred and stored its ID in SSM, attach the EIP to THAT instance
        # so the correct survivor gets the static IP. Falling through to self would give the EIP to this
        # second instance, which ASG then terminates when it scales back to desired=1 — orphaning the EIP.
        ssm_new_id_name = os.environ.get("SSM_NEW_INSTANCE_ID", "/pipeline/spot-new-instance-id")
        asg_name = os.environ.get("ASG_NAME")
        eip_target_id = instance_id  # default: attach to self if no prior deferral

        # List the live ASG instances so a stored deferral pointing at a dying instance isn't trusted blindly
        active_ids = None
        if asg_name:
            try:
                active_ids = _active_asg_instance_ids(asg_client, asg_name)
            except (ClientError, ValueError) as e:
                print(f"Warning: could not list ASG instances ({type(e).__name__}) — trusting the SSM record as-is")

        try:
            stored = ssm.get_parameter(Name=ssm_new_id_name)["Parameter"]["Value"]
            if stored not in ("none", instance_id) and (active_ids is None or stored in active_ids):
                # A previous launch already deferred — the EIP belongs on that instance, not self.
                eip_target_id = stored
                print(f"Edge case: EIP unattached and {stored} already deferred — attaching EIP there, not to self ({instance_id})")
            elif stored not in ("none", instance_id):
                # The deferred instance is already gone — attaching the EIP there would orphan it again
                print(f"Edge case: deferred instance {stored} is no longer live — attaching EIP to self ({instance_id})")
            else:
                print("Cleared spot-replacing SSM flags — old instance already gone before defer window")
        except Exception as e:
            print(f"Warning: could not read spot-new-instance-id from SSM ({type(e).__name__}) — attaching EIP to self")

        try:
            ssm.put_parameter(Name=ssm_flag_name, Value="false", Overwrite=True)
            ssm.put_parameter(Name=ssm_new_id_name, Value="none", Overwrite=True)
        except Exception as e:
            print(f"Warning: could not clear SSM flags in edge case: {e}")

        # Scale back to one instance only AFTER the EIP is attached and this hook is completed (see below)
        trim_to_target = bool(asg_name)

    else:
        eip_target_id = instance_id
        trim_to_target = False  # normal launch — nothing extra to remove

    print(f"Associating EIP to instance {eip_target_id}")

    # bind the static EIP to the correct instance; AllowReassociation handles the case
    # where the EIP is still associated with a terminating instance
    ec2.associate_address(
        InstanceId=eip_target_id,
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

    # Edge case only: drop every other live instance by name so the ASG can't pick the EIP holder.
    # A failure here is logged loudly, not raised — the spot_restored Lambda re-attaches an orphaned EIP as a safety net.
    if trim_to_target:
        try:
            removed = _keep_only(asg_client, os.environ["ASG_NAME"], eip_target_id)
            if not removed:
                print(f"No extra instances to remove — {eip_target_id} is the only live instance")
        except (ClientError, ValueError) as e:
            print(f"ERROR: could not remove extra instances ({type(e).__name__}: {e}) — "
                  f"ASG may still hold 2 instances; spot_restored will re-attach the EIP if it gets orphaned")
