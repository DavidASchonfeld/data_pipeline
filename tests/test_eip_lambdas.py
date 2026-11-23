# Offline tests for the two Lambdas that keep the static Elastic IP on the surviving spot instance.
# They replay the Nov 22 2026 double-replacement (EIP left attached to nothing) against fake AWS clients,
# so no network or AWS credentials are needed.
import importlib.util
import json
import os

import pytest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.offline

_LAMBDA_DIR = os.path.join(os.path.dirname(__file__), "..", "terraform", "lambda")


def _load(name):
    """Imports a Lambda file by path (terraform/lambda isn't a package)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_LAMBDA_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAWS:
    """One shared fake for ec2, ssm and autoscaling — records every call so tests can assert on them."""

    def __init__(self, eip_instance=None, ssm=None, asg_instances=None, desired=2, terminate_errors=()):
        self.eip_instance = eip_instance
        self.ssm = dict(ssm or {})
        self.asg_instances = list(asg_instances or [])  # [(instance_id, lifecycle_state)]
        self.desired = desired
        self.terminate_errors = list(terminate_errors)  # error codes raised before a terminate succeeds
        self.calls = []

    # ec2
    def describe_addresses(self, **kw):
        addr = {"AllocationId": "eipalloc-1"}
        if self.eip_instance:
            addr.update(AssociationId="eipassoc-1", InstanceId=self.eip_instance)
        return {"Addresses": [addr]}

    def associate_address(self, **kw):
        self.calls.append(("associate_address", kw["InstanceId"]))
        self.eip_instance = kw["InstanceId"]

    # ssm
    def get_parameter(self, Name):
        if Name not in self.ssm:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {"Value": self.ssm[Name]}}

    def put_parameter(self, Name, Value, Overwrite):
        self.ssm[Name] = Value

    # autoscaling
    def describe_auto_scaling_groups(self, **kw):
        insts = [{"InstanceId": i, "LifecycleState": s} for i, s in self.asg_instances]
        return {"AutoScalingGroups": [{"Instances": insts, "DesiredCapacity": self.desired}]}

    def terminate_instance_in_auto_scaling_group(self, InstanceId, ShouldDecrementDesiredCapacity):
        if self.terminate_errors:
            raise ClientError({"Error": {"Code": self.terminate_errors.pop(0)}}, "TerminateInstanceInAutoScalingGroup")
        self.calls.append(("terminate_in_asg", InstanceId, ShouldDecrementDesiredCapacity))

    def set_desired_capacity(self, AutoScalingGroupName, DesiredCapacity, **kw):
        self.calls.append(("set_desired_capacity", DesiredCapacity))

    def update_auto_scaling_group(self, **kw):
        self.calls.append(("update_asg", kw))

    def complete_lifecycle_action(self, **kw):
        self.calls.append(("complete_lifecycle_action", kw["LifecycleActionResult"]))

    def terminate_instances(self, InstanceIds):
        self.calls.append(("terminate_instances", InstanceIds))


@pytest.fixture
def env(monkeypatch):
    # The same env vars Terraform sets on both Lambdas
    for k, v in {
        "EIP_ALLOCATION_ID": "eipalloc-1",
        "ASG_NAME": "pipeline-asg",
        "SSM_SPOT_REPLACING": "/pipeline/spot-replacing",
        "SSM_NEW_INSTANCE_ID": "/pipeline/spot-new-instance-id",
    }.items():
        monkeypatch.setenv(k, v)


def _wire(monkeypatch, module, fake):
    """Points every boto3.client() call in the module at the fake, and makes retries instant."""
    monkeypatch.setattr(module.boto3, "client", lambda service, **kw: fake)
    if hasattr(module, "time"):
        monkeypatch.setattr(module.time, "sleep", lambda s: None)


def _launch_event(instance_id):
    msg = {
        "LifecycleTransition": "autoscaling:EC2_INSTANCE_LAUNCHING",
        "EC2InstanceId": instance_id,
        "LifecycleHookName": "pipeline-launch-eip",
        "AutoScalingGroupName": "pipeline-asg",
        "LifecycleActionToken": "tok",
    }
    return {"Records": [{"Sns": {"Message": json.dumps(msg)}}]}


# ── eip_reassociate (launch hook) ─────────────────────────────────────────────

def test_nov22_replay_keeps_eip_holder_and_removes_self_by_name(env, monkeypatch):
    # i-A was deferred during the spot warning; the old instance died; i-B launched as a 2nd replacement
    mod = _load("eip_reassociate")
    fake = FakeAWS(
        eip_instance=None,
        ssm={"/pipeline/spot-replacing": "true", "/pipeline/spot-new-instance-id": "i-A"},
        asg_instances=[("i-A", "InService"), ("i-B", "Pending:Wait")],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_launch_event("i-B"), None)

    assert ("associate_address", "i-A") in fake.calls
    assert ("terminate_in_asg", "i-B", True) in fake.calls  # extra removed by name, desired lowered
    assert not any(c[0] == "set_desired_capacity" for c in fake.calls)  # never lets ASG pick
    # hook completed before the extra instance is removed
    assert fake.calls.index(("complete_lifecycle_action", "CONTINUE")) < fake.calls.index(("terminate_in_asg", "i-B", True))
    assert fake.ssm["/pipeline/spot-new-instance-id"] == "none"
    assert fake.ssm["/pipeline/spot-replacing"] == "false"


def test_dead_deferred_instance_is_not_trusted(env, monkeypatch):
    # SSM still names i-A, but i-A is already terminating — attach to self and lower desired capacity
    mod = _load("eip_reassociate")
    fake = FakeAWS(
        ssm={"/pipeline/spot-replacing": "true", "/pipeline/spot-new-instance-id": "i-A"},
        asg_instances=[("i-A", "Terminating"), ("i-B", "Pending:Wait")],
        desired=2,
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_launch_event("i-B"), None)

    assert ("associate_address", "i-B") in fake.calls
    assert ("set_desired_capacity", 1) in fake.calls  # safe: i-B is the only live instance
    assert not any(c[0] == "terminate_in_asg" for c in fake.calls)


def test_busy_asg_is_retried_then_succeeds(env, monkeypatch):
    mod = _load("eip_reassociate")
    fake = FakeAWS(
        ssm={"/pipeline/spot-replacing": "true", "/pipeline/spot-new-instance-id": "i-A"},
        asg_instances=[("i-A", "InService"), ("i-B", "Pending:Wait")],
        terminate_errors=["ScalingActivityInProgress"],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_launch_event("i-B"), None)

    assert ("terminate_in_asg", "i-B", True) in fake.calls


def test_trim_failure_is_logged_not_raised(env, monkeypatch, capsys):
    # A non-retryable ASG error must not crash the Lambda after the EIP is already attached
    mod = _load("eip_reassociate")
    fake = FakeAWS(
        ssm={"/pipeline/spot-replacing": "true", "/pipeline/spot-new-instance-id": "i-A"},
        asg_instances=[("i-A", "InService"), ("i-B", "Pending:Wait")],
        terminate_errors=["AccessDenied"],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_launch_event("i-B"), None)

    assert ("associate_address", "i-A") in fake.calls
    assert "ERROR: could not remove extra instances" in capsys.readouterr().out


def test_normal_launch_attaches_to_self_without_touching_capacity(env, monkeypatch):
    mod = _load("eip_reassociate")
    fake = FakeAWS(ssm={"/pipeline/spot-replacing": "false"}, asg_instances=[("i-B", "Pending:Wait")])
    _wire(monkeypatch, mod, fake)

    mod.handler(_launch_event("i-B"), None)

    assert ("associate_address", "i-B") in fake.calls
    assert not any(c[0] in ("terminate_in_asg", "set_desired_capacity") for c in fake.calls)


# ── spot_restored (termination safety net) ────────────────────────────────────

def _terminated_event(instance_id):
    return {"detail": {"instance-id": instance_id, "state": "terminated"}}


def test_safety_net_reattaches_orphaned_eip_to_sole_survivor(env, monkeypatch):
    # The exact Nov 22 05:09 state: SSM already cleared, EIP holder gone, one InService survivor
    mod = _load("spot_restored")
    fake = FakeAWS(
        eip_instance=None,
        ssm={"/pipeline/spot-replacing": "false", "/pipeline/spot-new-instance-id": "none"},
        asg_instances=[("i-B", "InService")],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_terminated_event("i-A"), None)

    assert fake.calls == [("associate_address", "i-B")]


def test_safety_net_leaves_attached_eip_alone(env, monkeypatch):
    mod = _load("spot_restored")
    fake = FakeAWS(
        eip_instance="i-B",
        ssm={"/pipeline/spot-replacing": "false", "/pipeline/spot-new-instance-id": "none"},
        asg_instances=[("i-B", "InService")],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_terminated_event("i-other"), None)

    assert fake.calls == []


def test_safety_net_does_not_guess_between_two_instances(env, monkeypatch):
    mod = _load("spot_restored")
    fake = FakeAWS(
        eip_instance=None,
        ssm={"/pipeline/spot-replacing": "false", "/pipeline/spot-new-instance-id": "none"},
        asg_instances=[("i-A", "InService"), ("i-B", "InService")],
    )
    _wire(monkeypatch, mod, fake)

    mod.handler(_terminated_event("i-old"), None)

    assert fake.calls == []
