# Codifies existing AWS infrastructure so it can be reproduced with one terraform apply.
# Run import commands (see docs/architecture/TERRAFORM_IaC.md) to link existing resources to this state.

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

# ── Provider ──────────────────────────────────────────────────────────────────

provider "aws" {
  region = var.aws_region
}

# ── Caller identity ───────────────────────────────────────────────────────────

# Fetches the AWS account ID at plan time — avoids requiring it as a variable input.
data "aws_caller_identity" "current" {}

# ── VPC / Subnet lookup ───────────────────────────────────────────────────────

# Fetches the default VPC — used to place the ASG instances in the correct subnets.
data "aws_vpc" "default" {
  default = true
}

# All subnets in the default VPC — multi-AZ placement improves spot availability.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ── AMI lookup ────────────────────────────────────────────────────────────────

# Dynamically finds the latest Ubuntu 24.04 LTS AMI from Canonical — avoids hardcoding AMI IDs.
data "aws_ami" "ubuntu_24_04" {
  most_recent = true
  owners      = ["099720109477"] # Canonical's official AWS account ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── Security Group ────────────────────────────────────────────────────────────

# SSH + dashboard ingress — port 32147 exposed publicly; all other app ports via SSH tunnel.
resource "aws_security_group" "pipeline_sg" {
  name        = "pipeline-sg"
  description = "SSH-only ingress; all app ports accessed via SSH tunnel"

  ingress {
    description = "SSH from operators current IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  ingress {
    description = "Dashboard NodePort - public HTTP access to the Dash app"
    from_port   = 32147
    to_port     = 32147
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound (ECR pulls, apt, SEC EDGAR API calls)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "pipeline-sg"
    Project = "data-pipeline"
  }
}

# ── SSH Key Pair ──────────────────────────────────────────────────────────────

# Registers the public key in AWS so new instances get it injected into authorized_keys at boot — matches the local .pem in ~/.ssh/config.
resource "aws_key_pair" "pipeline" {
  key_name   = var.key_pair_name
  public_key = var.ssh_public_key

  tags = { Project = "data-pipeline" }
}

# ── IAM Role ──────────────────────────────────────────────────────────────────

# IAM role lets EC2 authenticate to ECR via instance metadata — no stored credentials needed.
resource "aws_iam_role" "ec2_ecr_role" {
  name = "ec2-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = { Project = "data-pipeline" }
}

# Grants push/pull access to ECR — used by flask.sh to build and deploy the dashboard image.
resource "aws_iam_role_policy_attachment" "ecr_power_user" {
  role       = aws_iam_role.ec2_ecr_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# Wraps the IAM role so it can be attached to the EC2 instance.
resource "aws_iam_instance_profile" "ec2_ecr_profile" {
  name = "ec2-ecr-role"  # matches the console-auto-created profile name — imported rather than recreated
  role = aws_iam_role.ec2_ecr_role.name
}

# ── Launch Template ───────────────────────────────────────────────────────────

# Defines the instance configuration used by the ASG — replaces the standalone aws_instance.
resource "aws_launch_template" "pipeline" {
  name_prefix   = "pipeline-"
  image_id      = data.aws_ami.ubuntu_24_04.id  # ARM Ubuntu 24.04 LTS (see AMI filter above)
  instance_type = var.instance_type              # default t4g.large — set in variables.tf
  key_name      = aws_key_pair.pipeline.key_name

  vpc_security_group_ids = [aws_security_group.pipeline_sg.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2_ecr_profile.name
  }

  # Boot script that starts K3s and refreshes credentials when the instance wakes up from a baked AMI
  user_data = base64encode(templatefile("${path.module}/user-data.sh.tpl", {
    aws_region   = var.aws_region
    ecr_registry = split("/", aws_ecr_repository.flask_app.repository_url)[0]
  }))

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_type           = "gp3"
      volume_size           = var.ebs_volume_size  # default 30 GiB — set in variables.tf
      encrypted             = true
      delete_on_termination = true  # safe to delete — the baked AMI preserves all data between sleep cycles
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "data-pipeline-ec2"
      Project = "data-pipeline"
    }
  }

  lifecycle {
    # prevent AMI drift from forcing launch template replacement on every apply
    ignore_changes = [image_id]
  }

  tags = { Project = "data-pipeline" }
}

# ── Auto Scaling Group ────────────────────────────────────────────────────────

# Sleep/wake ASG — starts at 0 (asleep) to save costs; Lambda sets desired=1 to wake it up
# when a visitor hits the dashboard. EIP is associated by Lambda on instance launch.
resource "aws_autoscaling_group" "pipeline" {
  name                = "pipeline-asg"
  min_size            = 0   # allow the group to scale down to zero so the instance can sleep
  max_size            = 1
  desired_capacity    = 0   # start asleep — the wake Lambda scales this to 1 when someone visits
  vpc_zone_identifier = data.aws_subnets.default.ids  # multi-AZ spot availability

  # Let the sleep/wake Lambdas control desired_capacity at runtime without Terraform resetting it
  lifecycle {
    ignore_changes = [desired_capacity]
  }

  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 0  # no guaranteed on-demand baseline
      on_demand_percentage_above_base_capacity = 0  # 100% spot
      spot_allocation_strategy                 = "capacity-optimized"  # minimizes interruptions
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.pipeline.id
        version            = "$Latest"
      }

      # diversify across two ARM pools — if t4g.large spot has no capacity, t4g.xlarge is used
      override {
        instance_type = "t4g.large"
      }
      override {
        instance_type = "t4g.xlarge"
      }
    }
  }

  tag {
    key                 = "Name"
    value               = "data-pipeline-ec2"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "data-pipeline"
    propagate_at_launch = true
  }
}

# ── Elastic IP ────────────────────────────────────────────────────────────────

# Static public IP so ~/.ssh/config and deploy configs never need updating after a stop/start.
resource "aws_eip" "pipeline_eip" {
  domain = "vpc"

  tags = {
    Name    = "pipeline-eip"
    Project = "data-pipeline"
  }
}

# ── EIP Re-association Lambda ─────────────────────────────────────────────────

# Packages the Python Lambda function as a ZIP for deployment.
data "archive_file" "eip_reassociate" {
  type        = "zip"
  source_file = "${path.module}/lambda/eip_reassociate.py"
  output_path = "${path.module}/lambda/eip_reassociate.zip"
}

# IAM role for the Lambda function — allows it to associate EIPs and signal ASG lifecycle hooks.
resource "aws_iam_role" "lambda_eip" {
  name = "pipeline-lambda-eip"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = { Project = "data-pipeline" }
}

resource "aws_iam_role_policy" "lambda_eip" {
  role = aws_iam_role.lambda_eip.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # CloudWatch Logs — needed for Lambda execution logs
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # EIP association — binds the static IP to the new spot instance
        Effect   = "Allow"
        Action   = ["ec2:AssociateAddress", "ec2:DisassociateAddress", "ec2:DescribeAddresses"]
        Resource = "*"
      },
      {
        # ASG lifecycle signal — tells ASG the hook work is done and it can mark instance InService
        Effect   = "Allow"
        Action   = "autoscaling:CompleteLifecycleAction"
        Resource = "*"
      }
    ]
  })
}

# Lambda function that re-associates the EIP when the ASG launches a new instance.
resource "aws_lambda_function" "eip_reassociate" {
  filename         = data.archive_file.eip_reassociate.output_path
  source_code_hash = data.archive_file.eip_reassociate.output_base64sha256
  function_name    = "pipeline-eip-reassociate"
  role             = aws_iam_role.lambda_eip.arn
  handler          = "eip_reassociate.handler"
  runtime          = "python3.12"
  timeout          = 30  # EIP association is fast; 30s is generous

  environment {
    variables = {
      EIP_ALLOCATION_ID   = aws_eip.pipeline_eip.id
      # Hardcoded names (not references) so removing spot_preempt.tf needs no main.tf edit.
      # eip_reassociate.py catches ParameterNotFound/AccessDenied and falls back to normal behaviour.
      SSM_SPOT_REPLACING  = "/pipeline/spot-replacing"
      SSM_NEW_INSTANCE_ID = "/pipeline/spot-new-instance-id"
    }
  }

  tags = { Project = "data-pipeline" }
}

# SNS topic that receives ASG lifecycle notifications and fans out to the Lambda.
resource "aws_sns_topic" "asg_lifecycle" {
  name = "pipeline-asg-lifecycle"
  tags = { Project = "data-pipeline" }
}

resource "aws_sns_topic_subscription" "lifecycle_lambda" {
  topic_arn = aws_sns_topic.asg_lifecycle.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.eip_reassociate.arn
}

# Allows SNS to invoke the Lambda function.
resource "aws_lambda_permission" "allow_sns" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.eip_reassociate.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.asg_lifecycle.arn
}

# IAM role for ASG to publish lifecycle notifications to SNS.
resource "aws_iam_role" "asg_lifecycle" {
  name = "pipeline-asg-lifecycle"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "autoscaling.amazonaws.com" }
    }]
  })

  tags = { Project = "data-pipeline" }
}

resource "aws_iam_role_policy" "asg_lifecycle" {
  role = aws_iam_role.asg_lifecycle.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = aws_sns_topic.asg_lifecycle.arn
    }]
  })
}

# Lifecycle hook: fires when ASG launches a new instance → triggers Lambda via SNS to associate EIP.
resource "aws_autoscaling_lifecycle_hook" "launch" {
  name                    = "pipeline-launch-eip"
  autoscaling_group_name  = aws_autoscaling_group.pipeline.name
  default_result          = "CONTINUE"  # if Lambda times out, instance still becomes InService
  heartbeat_timeout       = 300
  lifecycle_transition    = "autoscaling:EC2_INSTANCE_LAUNCHING"
  notification_target_arn = aws_sns_topic.asg_lifecycle.arn
  role_arn                = aws_iam_role.asg_lifecycle.arn
}

# ── ECR Repository ────────────────────────────────────────────────────────────

# Private ECR registry for the Flask dashboard image — K3s pulls from here on every deploy.
resource "aws_ecr_repository" "flask_app" {
  name                 = "my-flask-app"
  image_tag_mutability = "MUTABLE" # deploy.sh overwrites :latest on every build

  image_scanning_configuration {
    scan_on_push = true # Free ECR basic scanning — flags known CVEs in the Flask image on every push
  }

  tags = { Project = "data-pipeline" }
}

# Automatically removes untagged images after 1 day — every deploy overwrites :latest, leaving the old image untagged.
resource "aws_ecr_lifecycle_policy" "flask_app_lifecycle" {
  repository = aws_ecr_repository.flask_app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Remove untagged images after 1 day"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}
