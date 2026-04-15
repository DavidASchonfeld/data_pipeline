# Terraform Setup Guide

How to set up Terraform on a new machine and connect it to the existing AWS infrastructure.

**Prerequisites:** Homebrew installed on Mac.

---

## Step 1 — Install Terraform

Terraform runs on your Mac, not on EC2. Use HashiCorp's official Homebrew tap (not the community
formula — the official tap stays current):

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform -version   # should print a version number, e.g. Terraform v1.x.x
```

To upgrade later: `brew upgrade hashicorp/tap/terraform`

---

## Step 2 — Configure AWS SSO Authentication

Terraform uses AWS IAM Identity Center (SSO) to authenticate — no static access keys stored on disk.
See `TERRAFORM_AUTH.md` for why this approach was chosen.

Add this block to `~/.aws/config` (create the file if it does not exist):

```
[profile terraform-dev]
sso_start_url  = https://YOUR_SUBDOMAIN.awsapps.com/start
sso_region     = us-east-1
sso_account_id = YOUR_12_DIGIT_ACCOUNT_ID
sso_role_name  = AdministratorAccess
region         = us-east-1
output         = json
```

Where to find each value:

| Field | Where to find it |
|---|---|
| `sso_start_url` | AWS Console → IAM Identity Center → Settings → "AWS access portal URL" |
| `sso_account_id` | AWS Console → top-right account dropdown → 12-digit Account ID |
| `sso_role_name` | IAM Identity Center → AWS accounts → your account → assigned permission sets |

Set correct permissions on the file:
```bash
chmod 700 ~/.aws/
chmod 600 ~/.aws/config
```

Verify it works:
```bash
aws sso login --profile terraform-dev
aws sts get-caller-identity --profile terraform-dev
# Should print your account ID and ARN — no error
```

---

## Step 3 — Copy the Variables File

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

The example file already has the correct defaults. `terraform.tfvars` is gitignored and stays local.

---

## Step 4 — Initialize Terraform

```bash
./scripts/deploy/terraform.sh init
```

This downloads the AWS provider plugin (~100 MB) into `terraform/.terraform/`. You only need to
re-run this if you change the required provider version in `main.tf`.

**Success looks like:**
```
Terraform has been successfully initialized!
```

---

## Step 5 — Import Existing Resources

Because the infrastructure already exists in AWS, you need to tell Terraform about it before running
`apply`. Skipping this step would cause Terraform to try creating duplicate resources.

```bash
./scripts/deploy/terraform.sh import
```

This imports: IAM role, ECR repository, IAM instance profile, IAM policy attachment, key pair, and
Elastic IP. The Auto Scaling Group and its associated resources (launch template, Lambda functions,
CloudFront distribution, S3 bucket) are created fresh by `apply` — they do not need importing.

---

## Step 6 — Verify the Setup Is Complete

```bash
./scripts/deploy/terraform.sh plan
```

After a successful import, the plan will show a few items — **all of them are expected**:

| Plan output | What it means | Action |
|---|---|---|
| `~ update aws_security_group.pipeline_sg` | Your current IP differs from last apply | Run `apply` |
| `+ create aws_ecr_lifecycle_policy.flask_app_lifecycle` | New resource — didn't exist before | Run `apply` |

**None of these require additional imports.** If you see resources marked `+ create` that should
already exist (IAM role, ECR repository, Elastic IP), they were not imported — re-run `import`.

**With the Auto Scaling Group setup,** `terraform apply` creates resources rather than replacing a
single instance. The ASG launches its first spot instance automatically after `apply` completes.

Once apply completes, re-run `plan` to confirm:

```
No changes. Infrastructure is up-to-date.
```

(Except for the security group CIDR, which will drift again if your IP changes.)

---

## Step 7 — Confirm SSH Works

```bash
ssh ec2-stock
```

If this connects, the security group rule is live in AWS for your current IP. If it times out, your
IP has changed since the last apply — run:

```bash
./scripts/deploy/terraform.sh apply
```

This auto-detects your new IP and updates the security group in ~10 seconds.

---

## MFA Setup (Required for SSO)

AWS IAM Identity Center requires MFA. During the first `aws sso login`, you will be prompted to
register an authenticator app:

1. Choose "Authenticator app"
2. Open Duo Mobile (or any TOTP app) → tap + → scan the QR code → enter the 6-digit code to confirm
3. Every future login will prompt for a code from the app

**If you lose access to your MFA device:** AWS Console (root login) → IAM Identity Center →
Users → your username → MFA devices tab → remove the device → re-register.

Enable cloud backup in your authenticator app (e.g. Duo Restore to iCloud) so you are not locked
out if you lose your phone.
