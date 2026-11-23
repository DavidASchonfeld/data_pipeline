# Incident: Elastic IP Left Attached to Nothing After a Double Spot Replacement

**Date:** 2026-11-22
**Severity:** Medium (the server was unreachable at its static IP for ~13 hours; the instance itself kept running)
**Status:** Resolved. The Lambdas now remove the extra instance by name, a termination safety net re-attaches an orphaned EIP, and the deploy pre-flight re-attaches it too

---

## Summary

AWS sent a spot interruption notice at 05:03 UTC. My recovery automation started a replacement, but two replacement instances ended up launching. The launch-hook Lambda attached the Elastic IP (52.70.211.1) to the first replacement, then scaled the Auto Scaling group (ASG) back to one instance. **The ASG chose to terminate the instance holding the EIP.** The surviving instance had no public IP at 52.70.211.1, so SSH and every deploy timed out.

I first noticed it when `./scripts/deploy.sh --run-script` failed with:

```
ssh: connect to host 52.70.211.1 port 22: Operation timed out
```

The same family of failure happened on 2026-05-10. That fix made the Lambda attach the EIP to the *right* instance, but it still let the ASG decide which instance to remove.

---

## Timeline (UTC)

| Time | Event |
|------|-------|
| 05:03:27 | `spot-preempt` Lambda gets the interruption notice for `i-031d…`; sets SSM `spot-replacing=true`; ASG to desired=2 |
| 05:03:41 | ASG launches `i-0417…`; `eip-reassociate` defers (EIP still on the old instance) and stores `i-0417…` in SSM |
| 05:07:01 | AWS reclaims the old instance `i-031d…`; ASG launches `i-09b2…` to keep desired=2 |
| 05:07:08 | `spot-restored` runs for `i-031d…`, but by now SSM says `none`, so it does nothing |
| ~05:07 | `eip-reassociate` (launch hook for `i-09b2…`) takes the edge-case path: attaches the EIP to `i-0417…`, clears SSM, sets desired=1 |
| 05:07:22 | **ASG picks `i-0417…` (the EIP holder) to terminate** |
| 05:09:09 | `spot-restored` runs for `i-0417…`: SSM says `none`, so "not a spot replacement, nothing to do" |
| ~18:30 | Fixed Lambdas applied; the deploy pre-flight re-attached the EIP to `i-09b2…` |

---

## Root cause

**1. Letting the ASG pick (the real bug).** The edge-case path in `terraform/lambda/eip_reassociate.py` lowered capacity with `set_desired_capacity(1)`. That tells the ASG "drop one instance, your choice". Its termination policy doesn't know or care which instance holds the EIP.

**2. No safety net after the SSM record was cleared.** `spot_restored.py` only acted when SSM held a pending replacement ID. The launch hook had already cleared that record, so when the EIP holder died, nothing re-attached the EIP. Both Lambdas fire only on events, and after 05:09 no more events came, so nothing could recover it on its own.

**3. A half-applied earlier fix.** Terraform showed that the `spot-restored` IAM policy in AWS was missing `autoscaling:DescribeAutoScalingGroups` and `ec2:TerminateInstances`, which its code already used. An earlier change updated the code but never applied the policy, so that step could only have failed with AccessDenied.

**4. The pre-flight check stayed quiet.** `_check_instance_state` in the deploy scripts is designed to catch exactly this. But my AWS SSO login had expired, and the check silently skipped itself when the login failed. It also wasn't called at all in `--run-script` mode.

---

## Fix

**Launch hook (`eip_reassociate.py`)**
- After attaching the EIP and completing the lifecycle hook, it removes every other live instance **by name** with `TerminateInstanceInAutoScalingGroup(ShouldDecrementDesiredCapacity=True)`. That terminates exactly the named instance and lowers desired capacity in one call, so the ASG has no choice to make.
- It only calls `set_desired_capacity(1)` when the EIP holder is already the only live instance, so the ASG can't pick wrong.
- It no longer trusts a stored deferral blindly. If the stored instance isn't live any more, it attaches the EIP to itself.
- It retries "ASG busy" errors (bounded: 3 attempts, 2s then 4s). The Lambda timeout went from 30s to 60s to fit the retries.
- The IAM policy gained `DescribeAutoScalingGroups` and `TerminateInstanceInAutoScalingGroup`.

**Termination safety net (`spot_restored.py`)**
- On every instance termination, even when no replacement is being tracked, it checks the EIP. If the EIP is attached to nothing and exactly one ASG instance is InService, it attaches the EIP there. With zero or two-plus candidates it logs and does nothing rather than guess. This alone would have fixed today's incident at 05:09.

**Deploy pre-flight (`scripts/deploy/common.sh`, `scripts/deploy.sh`)**
- `_check_instance_state` re-attaches an orphaned EIP when exactly one pipeline instance is running, and stops with a clear message in any ambiguous case.
- An expired AWS login now prints a loud warning with the `aws sso login` command instead of silently skipping the checks.
- The checks now also run for `--run-script`, `--fix-ml-venv` and right after `terraform apply` in `--provision`.

Offline tests in `tests/test_eip_lambdas.py` replay this incident against fake AWS clients.

---

## Lessons

- **Never let an autoscaler choose when the choice matters.** If one instance holds state (an IP, a volume, a lock), remove the *others* by name instead of lowering a count.
- **Every event-driven fix needs a fallback that doesn't depend on the event.** Here, the fallback is any later termination plus every deploy.
- **A check that skips itself silently is worse than no check.** It hides the real cause behind a generic timeout.
- **Run `terraform plan` after changing Lambda code.** IAM and code drift apart quietly.
