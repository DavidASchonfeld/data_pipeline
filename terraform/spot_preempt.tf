# Spot instance proactive replacement infrastructure.
#
# When AWS issues a 2-minute spot termination warning, the spot_preempt Lambda immediately
# boots a replacement instance. The modified EIP Lambda defers IP reassociation until
# the old instance actually terminates, keeping users connected for the full warning window.
# The spot_restored Lambda then moves the EIP and resets ASG capacity.
#
# ON/OFF SWITCH: deleting this file removes every resource below with a single terraform apply.
# No Python code changes are needed — eip_reassociate.py auto-detects the missing SSM params.
#
# Dependencies: aws_autoscaling_group.pipeline and aws_eip.pipeline_eip from main.tf.
# If you remove the ASG (standalone EC2), delete this file first.

# ── SSM Parameters ────────────────────────────────────────────────────────────

# Flag set to "true" when a spot replacement is in progress — tells EIP Lambda to defer reassociation.
resource "aws_ssm_parameter" "spot_replacing" {
  name      = "/pipeline/spot-replacing"
  type      = "String"
  value     = "false"
  overwrite = true  # allow re-apply without error if parameter already exists in AWS

  tags = { Project = "data-pipeline" }

  lifecycle {
    # Lambdas write this flag at runtime — Terraform must not reset it between deployments
    ignore_changes = [value]
  }
}

# Stores the ID of the new (booting) instance so spot_restored Lambda knows where to move the EIP.
resource "aws_ssm_parameter" "spot_new_instance_id" {
  name      = "/pipeline/spot-new-instance-id"
  type      = "String"
  value     = "none"
  overwrite = true  # allow re-apply without error if parameter already exists in AWS

  tags = { Project = "data-pipeline" }

  lifecycle {
    # Lambdas write this at runtime — Terraform must not reset it between deployments
    ignore_changes = [value]
  }
}

# ── Spot Preempt Lambda ───────────────────────────────────────────────────────
# Triggered by EventBridge spot warning → scales ASG to 2 so replacement boots immediately.

data "archive_file" "spot_preempt" {
  type        = "zip"
  source_file = "${path.module}/lambda/spot_preempt.py"
  output_path = "${path.module}/lambda/spot_preempt.zip"
}

# IAM role the spot_preempt Lambda runs as.
resource "aws_iam_role" "lambda_spot_preempt" {
  name = "pipeline-lambda-spot-preempt"

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

# Grants only what spot_preempt needs: scale the ASG, set the SSM flag, write logs.
resource "aws_iam_role_policy" "lambda_spot_preempt" {
  role = aws_iam_role.lambda_spot_preempt.name

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
        # Scale ASG to 2 — UpdateAutoScalingGroup raises max_size; SetDesiredCapacity starts the launch
        Effect   = "Allow"
        Action   = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:SetDesiredCapacity"]
        Resource = "*"  # ASG actions do not support resource-level permissions
      },
      {
        # Write the spot-replacing flag so EIP Lambda defers reassociation during the warning window
        Effect   = "Allow"
        Action   = "ssm:PutParameter"
        Resource = aws_ssm_parameter.spot_replacing.arn
      }
    ]
  })
}

resource "aws_lambda_function" "spot_preempt" {
  filename         = data.archive_file.spot_preempt.output_path
  source_code_hash = data.archive_file.spot_preempt.output_base64sha256
  function_name    = "pipeline-spot-preempt"
  role             = aws_iam_role.lambda_spot_preempt.arn
  handler          = "spot_preempt.handler"
  runtime          = "python3.12"
  timeout          = 15  # SSM write + two ASG API calls; 15s is generous

  environment {
    variables = {
      ASG_NAME           = aws_autoscaling_group.pipeline.name
      SSM_SPOT_REPLACING = aws_ssm_parameter.spot_replacing.name
    }
  }

  tags = { Project = "data-pipeline" }
}

# EventBridge rule: fires when AWS issues a spot termination notice (2-minute warning).
resource "aws_cloudwatch_event_rule" "spot_interruption" {
  name        = "pipeline-spot-interruption"
  description = "Fires on EC2 spot interruption warning — triggers proactive replacement Lambda"

  event_pattern = jsonencode({
    source        = ["aws.ec2"]
    "detail-type" = ["EC2 Spot Instance Interruption Warning"]
  })

  tags = { Project = "data-pipeline" }
}

resource "aws_cloudwatch_event_target" "spot_preempt_lambda" {
  rule = aws_cloudwatch_event_rule.spot_interruption.name
  arn  = aws_lambda_function.spot_preempt.arn
}

# Allows EventBridge to invoke the spot_preempt Lambda.
resource "aws_lambda_permission" "allow_eventbridge_spot_preempt" {
  statement_id  = "AllowEventBridgeSpotInterruption"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.spot_preempt.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.spot_interruption.arn
}

# ── EIP Lambda SSM Extension ──────────────────────────────────────────────────
# Adds SSM read/write to the existing EIP Lambda (defined in main.tf) so it can check
# the spot-replacing flag and store the new instance ID. Removed automatically with this file.

resource "aws_iam_role_policy" "lambda_eip_ssm" {
  name = "pipeline-lambda-eip-ssm"
  role = aws_iam_role.lambda_eip.name  # extends the base EIP Lambda role from main.tf

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # Read the spot-replacing flag; write the new instance ID for spot_restored to consume
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:PutParameter"]
      Resource = [
        aws_ssm_parameter.spot_replacing.arn,
        aws_ssm_parameter.spot_new_instance_id.arn,
      ]
    }]
  })
}

# ── Spot Restored Lambda ──────────────────────────────────────────────────────
# Triggered by EC2 instance termination → moves EIP to replacement, resets ASG.

data "archive_file" "spot_restored" {
  type        = "zip"
  source_file = "${path.module}/lambda/spot_restored.py"
  output_path = "${path.module}/lambda/spot_restored.zip"
}

# IAM role the spot_restored Lambda runs as.
resource "aws_iam_role" "lambda_spot_restored" {
  name = "pipeline-lambda-spot-restored"

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

# Grants only what spot_restored needs: reassociate EIP, reset ASG, clear SSM flags.
resource "aws_iam_role_policy" "lambda_spot_restored" {
  role = aws_iam_role.lambda_spot_restored.name

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
        # Move the EIP from the terminated spot instance to the new replacement instance
        Effect   = "Allow"
        Action   = ["ec2:AssociateAddress", "ec2:DisassociateAddress", "ec2:DescribeAddresses"]
        Resource = "*"  # EIP actions require * — no resource-level restriction available
      },
      {
        # Reset ASG max=1 desired=1 after the old instance is confirmed gone;
        # DescribeAutoScalingGroups lists instances so the non-EIP one can be terminated explicitly
        Effect   = "Allow"
        Action   = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:SetDesiredCapacity", "autoscaling:DescribeAutoScalingGroups"]
        Resource = "*"  # ASG actions do not support resource-level permissions
      },
      {
        # Terminate the non-EIP instance after spot replacement so ASG scale-down is deterministic
        Effect   = "Allow"
        Action   = ["ec2:TerminateInstances"]
        Resource = "*"
      },
      {
        # Read the waiting instance ID; clear both flags once replacement is complete
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = [
          aws_ssm_parameter.spot_replacing.arn,
          aws_ssm_parameter.spot_new_instance_id.arn,
        ]
      }
    ]
  })
}

resource "aws_lambda_function" "spot_restored" {
  filename         = data.archive_file.spot_restored.output_path
  source_code_hash = data.archive_file.spot_restored.output_base64sha256
  function_name    = "pipeline-spot-restored"
  role             = aws_iam_role.lambda_spot_restored.arn
  handler          = "spot_restored.handler"
  runtime          = "python3.12"
  timeout          = 30  # EIP association + ASG reset + SSM writes; 30s is generous

  environment {
    variables = {
      EIP_ALLOCATION_ID   = aws_eip.pipeline_eip.id
      ASG_NAME            = aws_autoscaling_group.pipeline.name
      SSM_SPOT_REPLACING  = aws_ssm_parameter.spot_replacing.name
      SSM_NEW_INSTANCE_ID = aws_ssm_parameter.spot_new_instance_id.name
    }
  }

  tags = { Project = "data-pipeline" }
}

# EventBridge rule: fires on every EC2 instance termination in this account.
# The Lambda checks the SSM flag and exits immediately if it's not a spot replacement — cheap and safe.
resource "aws_cloudwatch_event_rule" "ec2_terminated" {
  name        = "pipeline-ec2-terminated"
  description = "Fires on EC2 instance termination — spot_restored Lambda ignores non-spot events via SSM check"

  event_pattern = jsonencode({
    source        = ["aws.ec2"]
    "detail-type" = ["EC2 Instance State-change Notification"]
    detail = {
      state = ["terminated"]
    }
  })

  tags = { Project = "data-pipeline" }
}

resource "aws_cloudwatch_event_target" "spot_restored_lambda" {
  rule = aws_cloudwatch_event_rule.ec2_terminated.name
  arn  = aws_lambda_function.spot_restored.arn
}

# Allows EventBridge to invoke the spot_restored Lambda.
resource "aws_lambda_permission" "allow_eventbridge_spot_restored" {
  statement_id  = "AllowEventBridgeEC2Terminated"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.spot_restored.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ec2_terminated.arn
}
