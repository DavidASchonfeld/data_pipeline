# Terraform Overview

## What This Is

Terraform files that describe the AWS infrastructure for this project so it can be rebuilt with a
single command. No new resources are created by these files — everything defined here already exists
in AWS. The benefit is that disaster recovery or setup on a new machine goes from "click through ten
AWS Console screens and hope you remember every setting" to `./scripts/deploy/terraform.sh apply`.

---

## What Terraform Manages

| Resource | Terraform Name | Purpose |
|---|---|---|
| Launch template | `aws_launch_template.pipeline` | Defines the blueprint for new instances: ARM Ubuntu 24.04, t4g.large spot (falls back to t4g.xlarge), 30 GB disk, startup script |
| Auto Scaling Group | `aws_autoscaling_group.pipeline` | Keeps one spot instance running at all times (min=1, max=2); launches replacements automatically across multiple availability zones |
| Security group | `aws_security_group.pipeline_sg` | SSH inbound from your IP only; all app ports blocked publicly (accessed via SSH tunnel) |
| Elastic IP | `aws_eip.pipeline_eip` | Static public IP so your SSH config and CloudFront never need updating after instance replacement |
| CloudFront distribution | `aws_cloudfront_distribution.pipeline` | CDN in front of the EC2 instance; serves a static "switching servers" page from S3 during spot replacement |
| S3 bucket (loading page) | `aws_s3_bucket.loading_page` | Holds the static page shown to dashboard visitors while a replacement instance is booting |
| Lambda — EIP reassociate | `eip_reassociate` (in `cloudfront.tf`) | Runs automatically on every ASG launch event; moves the Elastic IP to the new instance |
| Lambda — spot preempt | `spot_preempt` (in `cloudfront.tf`) | Triggered when AWS issues a 2-minute spot interruption warning; scales ASG to 2 so a replacement boots during the warning window |
| Lambda — spot restored | `spot_restored` (in `cloudfront.tf`) | Triggered when the interrupted instance terminates; moves EIP to the replacement and resets ASG back to desired=1 |
| ECR repository | `aws_ecr_repository.flask_app` | Private Docker registry for the Flask dashboard image |
| ECR lifecycle policy | `aws_ecr_lifecycle_policy.flask_app_lifecycle` | Auto-removes untagged images after 1 day to prevent storage cost accumulation |
| IAM role | `aws_iam_role.ec2_ecr_role` | Lets EC2 authenticate to ECR via instance metadata — no credentials stored on disk |
| Instance profile | `aws_iam_instance_profile.ec2_ecr_profile` | Attaches the IAM role to the EC2 instance |

---

## What Terraform Does NOT Manage

| Thing | Where it lives instead |
|---|---|
| K3s, Airflow, Kafka, MLflow, MariaDB | Baked into the AMI via `scripts/deploy.sh --bake-ami` |
| K8s manifests, Helm values, DAG files | Deployed by `scripts/deploy.sh` |
| Flask Docker image | Built and pushed by `scripts/deploy/flask.sh` |

These are runtime concerns, not infrastructure. Mixing them into Terraform would mean running
`terraform apply` every time you update a DAG file — that is the wrong tool for that job.

---

## deploy.sh vs. Terraform

These two tools solve different problems and should never be confused:

| Tool | What it does | Touches the EC2 instance? |
|---|---|---|
| `deploy.sh` | SSHes in, rsyncs DAGs, builds a Docker image, runs Helm upgrade | Modifies software *on* the instance |
| `terraform.sh` | Manages the AWS resources themselves (the ASG, the IP, the security group, the Lambda functions) | Can create, modify, or destroy infrastructure |

Running `deploy.sh` 100 times always uses the same running instance. Running `terraform apply` 100
times with no changes to the `.tf` files also does nothing — Terraform is idempotent and only acts
on differences.

---

## File Structure

```
terraform/
├── main.tf               — EC2/ASG/EIP/IAM/ECR/security group/key pair resources
├── cloudfront.tf         — CloudFront distribution, S3 loading page, Lambda functions, SNS topic, EventBridge rules
├── variables.tf          — input variable declarations (no actual values)
├── outputs.tf            — values printed after apply (EIP, ECR URL, CloudFront domain)
├── user-data.sh.tpl      — instance boot script (starts K3s, refreshes ECR credentials)
├── lambda/               — source code for the three Lambda functions
│   ├── eip_reassociate.py
│   ├── spot_preempt.py
│   └── spot_restored.py
└── terraform.tfvars.example  — template to copy to terraform.tfvars and fill in
```

---

## What Is and Is Not Committed to Git

**Committed (safe to be public):**

| File | Why it is safe |
|---|---|
| `main.tf` | Resource definitions only — no secrets, no real IDs |
| `cloudfront.tf` | Resource definitions only — no secrets, no real IDs |
| `variables.tf` | Variable declarations only — no actual values |
| `outputs.tf` | Output definitions only — no actual values |
| `terraform.tfvars.example` | A blank template with no real values filled in |
| `lambda/` | Lambda function source code — no secrets, no credentials |

**Gitignored (stays on your machine only):**

| File / Directory | Why it is excluded |
|---|---|
| `terraform.tfvars` | Your real variable values (key pair name, region) |
| `terraform.tfstate` / `terraform.tfstate.backup` | Terraform's local record of which real AWS resources it manages. Contains real resource IDs — not secrets, but no reason to commit them. |
| `.terraform/` | The downloaded AWS provider plugin binary (~100 MB). Re-downloaded automatically by `terraform init`. |
| `.terraform.lock.hcl` | Provider version lock file. Gitignored for a solo project; on a team you would commit this so everyone uses the same provider version. |

**Important:** `terraform.tfstate` contains your real AWS account ID and resource IDs. Confirm it
has never been `git add`-ed. Running `git status` from the project root should show the entire
`terraform/` directory as untracked (`?? terraform/`) — meaning nothing inside it has been staged.
