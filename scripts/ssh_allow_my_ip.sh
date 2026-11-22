#!/bin/bash
# Lets me SSH into the pipeline server again after my home IP address changes — without a full deploy.
# The server's firewall (security group "pipeline-sg") only accepts SSH from one IP. This script looks up
# that firewall and my current public IP at run time, adds my new IP, then removes the old one(s).
# Nothing environment-specific is hard-coded here: the group is found by its Name tag, the region comes
# from .env.deploy, and the AWS login comes from my local AWS CLI profile.
#
# Usage:
#   ./scripts/ssh_allow_my_ip.sh            # swap the SSH rule to my current IP, then test SSH
#   ./scripts/ssh_allow_my_ip.sh --dry-run  # only show what would change
#
# Note: Terraform owns this rule. The next `./scripts/deploy.sh --provision` re-sets it to whatever my
# IP is then, so the quick fix here never leaves lasting drift.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_DEPLOY="$PROJECT_ROOT/.env.deploy"

# Same defaults the deploy scripts use, so this works with no extra setup
AWS_PROFILE_NAME="${AWS_PROFILE:-terraform-dev}"
SG_NAME_TAG="pipeline-sg"
SSH_HOST_ALIAS="${EC2_HOST:-ec2-stock}"
DRY_RUN=false

# Print an error with context and stop — every failure path goes through here
die() { echo "✗ $*" >&2; exit 1; }

# ── Parse arguments ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg (use --dry-run or --help)" ;;
    esac
done

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v aws  >/dev/null || die "AWS CLI not found — install it first (brew install awscli)"
command -v curl >/dev/null || die "curl not found"

# Read only AWS_REGION from .env.deploy (the full deploy config isn't needed for this)
if [ -z "${AWS_REGION:-}" ] && [ -f "$ENV_DEPLOY" ]; then
    AWS_REGION="$(grep -E '^AWS_REGION=' "$ENV_DEPLOY" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi
[ -n "${AWS_REGION:-}" ] || die "AWS_REGION is not set (export it, or add it to .env.deploy)"

# Confirm the AWS login works before touching anything (an expired session is the usual culprit)
aws sts get-caller-identity --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    --cli-read-timeout 15 --cli-connect-timeout 10 >/dev/null 2>&1 \
    || die "AWS profile '$AWS_PROFILE_NAME' is not logged in — run: aws sso login --profile $AWS_PROFILE_NAME (or refresh your keys)"

# ── Look up my current public IP ──────────────────────────────────────────────
# AWS's own IP-echo service, with a short timeout and a few retries in case the network blips
MY_IP="$(curl -fsS --max-time 5 --retry 3 --retry-delay 1 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')" \
    || die "Could not look up my public IP (checkip.amazonaws.com unreachable)"
# Make sure the answer really is an IPv4 address before putting it in a firewall rule
[[ "$MY_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "Unexpected reply from the IP lookup: '$MY_IP'"
MY_CIDR="$MY_IP/32"

# ── Find the firewall (security group) by its Name tag ────────────────────────
SG_ID="$(aws ec2 describe-security-groups --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    --cli-read-timeout 15 --cli-connect-timeout 10 \
    --filters "Name=tag:Name,Values=$SG_NAME_TAG" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)" \
    || die "AWS call failed while looking up security group '$SG_NAME_TAG'"
[[ "$SG_ID" == sg-* ]] || die "No security group tagged Name=$SG_NAME_TAG in $AWS_REGION"

# Every IP currently allowed on port 22 (normally just one)
OLD_CIDRS="$(aws ec2 describe-security-groups --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    --cli-read-timeout 15 --cli-connect-timeout 10 --group-ids "$SG_ID" \
    --query 'SecurityGroups[0].IpPermissions[?FromPort==`22` && ToPort==`22`].IpRanges[].CidrIp' \
    --output text 2>/dev/null)" \
    || die "AWS call failed while reading the SSH rule on $SG_ID"
OLD_CIDRS="$(echo "$OLD_CIDRS" | tr '\t' '\n' | grep -v '^None$' | grep -v '^$' || true)"

echo "Firewall:        $SG_NAME_TAG"
echo "SSH allowed now: ${OLD_CIDRS:-<nothing>}" | paste -sd' ' -
echo "My IP now:       $MY_CIDR"

# Nothing to do if my IP is already the only one allowed
if [ "$OLD_CIDRS" = "$MY_CIDR" ]; then
    echo "✓ Already allowed — nothing to change."
    exit 0
fi

if $DRY_RUN; then
    echo "(dry run) Would allow $MY_CIDR and remove: $(echo "$OLD_CIDRS" | grep -vx "$MY_CIDR" | paste -sd' ' -)"
    exit 0
fi

# ── Swap the rule: add the new IP first, then remove the old ones ─────────────
# Adding before removing means there's never a moment where nobody can get in
if ! echo "$OLD_CIDRS" | grep -qx "$MY_CIDR"; then
    aws ec2 authorize-security-group-ingress --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
        --cli-read-timeout 15 --cli-connect-timeout 10 \
        --group-id "$SG_ID" --ip-permissions \
        "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$MY_CIDR,Description=SSH from operators current IP only}]" \
        >/dev/null || die "Could not add $MY_CIDR to the SSH rule (check IAM permissions)"
    echo "✓ Allowed $MY_CIDR"
fi

for cidr in $OLD_CIDRS; do
    [ "$cidr" = "$MY_CIDR" ] && continue
    aws ec2 revoke-security-group-ingress --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
        --cli-read-timeout 15 --cli-connect-timeout 10 \
        --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$cidr" >/dev/null \
        || die "Added my IP but could not remove old rule $cidr — remove it in the AWS console"
    echo "✓ Removed old IP $cidr"
done

# ── Prove it worked ───────────────────────────────────────────────────────────
# A quick, non-interactive SSH test; a failure here usually means the server itself is down
if ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST_ALIAS" true 2>/dev/null; then
    echo "✓ SSH works — connect with: ssh $SSH_HOST_ALIAS"
else
    echo "⚠ Rule updated, but SSH to '$SSH_HOST_ALIAS' still fails."
    echo "  The server may be stopped or being replaced (spot) — check the instance in the EC2 console."
    exit 1
fi
