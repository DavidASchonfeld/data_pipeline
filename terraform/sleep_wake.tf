# Sleep/Wake architecture — automatically shuts down the EC2 instance when idle and
# boots it back up when a visitor hits the dashboard URL. Saves ~90% on compute costs
# during nights/weekends when nobody is using the pipeline.

# ── SSM Parameters ───────────────────────────────────────────────────────────

# Stores the timestamp of the last dashboard visit — Sleep Lambda reads this to decide if the server is idle.
resource "aws_ssm_parameter" "last_activity" {
  name      = "/pipeline/last-activity-timestamp"
  type      = "String"
  value     = "0"
  overwrite = true  # allow apply to succeed even if the parameter already exists in AWS (e.g. from a prior partial run)

  tags = { Project = "data-pipeline" }

  lifecycle {
    # Lambda updates this value at runtime — Terraform should not overwrite it on subsequent applies
    ignore_changes = [value]
  }
}

# Flag that deploy.sh sets to "true" during deploys — prevents the Sleep Lambda from shutting down mid-deploy.
resource "aws_ssm_parameter" "deploy_active" {
  name      = "/pipeline/deploy-active"
  type      = "String"
  value     = "false"
  overwrite = true  # allow apply to succeed even if the parameter already exists in AWS (e.g. from a prior partial run)

  tags = { Project = "data-pipeline" }

  lifecycle {
    # deploy.sh toggles this at runtime — Terraform should not reset it
    ignore_changes = [value]
  }
}

# ── Wake Lambda ──────────────────────────────────────────────────────────────

# Packages the Wake Lambda Python file as a ZIP for deployment.
data "archive_file" "wake" {
  type        = "zip"
  source_file = "${path.module}/lambda/wake.py"
  output_path = "${path.module}/lambda/wake.zip"
}

# IAM role that the Wake Lambda runs as — allows it to start the server and update the activity timestamp.
resource "aws_iam_role" "lambda_wake" {
  name = "pipeline-lambda-wake"

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

# Grants the Wake Lambda only the permissions it needs — start the ASG, update the timestamp, and write logs.
resource "aws_iam_role_policy" "lambda_wake" {
  role = aws_iam_role.lambda_wake.name

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
        # ASG read + scale up — checks current capacity and sets desired to 1 to boot the server
        Effect   = "Allow"
        Action   = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:SetDesiredCapacity"]
        Resource = "*"  # ASG actions do not support resource-level permissions
      },
      {
        # SSM write — records the current time so the Sleep Lambda knows someone visited recently
        Effect   = "Allow"
        Action   = "ssm:PutParameter"
        Resource = aws_ssm_parameter.last_activity.arn
      }
    ]
  })
}

# Lambda function that boots the server when a visitor hits the dashboard URL.
resource "aws_lambda_function" "wake" {
  filename         = data.archive_file.wake.output_path
  source_code_hash = data.archive_file.wake.output_base64sha256
  function_name    = "pipeline-wake"
  role             = aws_iam_role.lambda_wake.arn
  handler          = "wake.handler"
  runtime          = "python3.12"
  timeout          = 30  # ASG API call is fast; 30s is generous

  environment {
    variables = {
      ASG_NAME      = aws_autoscaling_group.pipeline.name
      DASHBOARD_EIP = aws_eip.pipeline_eip.public_ip
      SSM_PARAM     = aws_ssm_parameter.last_activity.name
    }
  }

  tags = { Project = "data-pipeline" }
}

# ── Sleep Lambda ─────────────────────────────────────────────────────────────

# Packages the Sleep Lambda Python file as a ZIP for deployment.
data "archive_file" "sleep" {
  type        = "zip"
  source_file = "${path.module}/lambda/sleep.py"
  output_path = "${path.module}/lambda/sleep.zip"
}

# IAM role that the Sleep Lambda runs as — allows it to check activity and shut down the server.
resource "aws_iam_role" "lambda_sleep" {
  name = "pipeline-lambda-sleep"

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

# Grants the Sleep Lambda only the permissions it needs — check activity, read deploy status, scale down, and write logs.
resource "aws_iam_role_policy" "lambda_sleep" {
  role = aws_iam_role.lambda_sleep.name

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
        # ASG read + scale down — checks current capacity and sets desired to 0 to shut down the server
        Effect   = "Allow"
        Action   = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:SetDesiredCapacity"]
        Resource = "*"  # ASG actions do not support resource-level permissions
      },
      {
        # SSM read — checks the last activity timestamp and whether a deploy is in progress
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          aws_ssm_parameter.last_activity.arn,
          aws_ssm_parameter.deploy_active.arn
        ]
      }
    ]
  })
}

# Lambda function that shuts down the server if nobody has visited the dashboard recently.
resource "aws_lambda_function" "sleep" {
  filename         = data.archive_file.sleep.output_path
  source_code_hash = data.archive_file.sleep.output_base64sha256
  function_name    = "pipeline-sleep"
  role             = aws_iam_role.lambda_sleep.arn
  handler          = "sleep.handler"
  runtime          = "python3.12"
  timeout          = 15  # SSM reads + ASG describe are fast; 15s is plenty

  environment {
    variables = {
      ASG_NAME            = aws_autoscaling_group.pipeline.name
      IDLE_TIMEOUT_MINUTES = tostring(var.idle_timeout_minutes)
      SSM_LAST_ACTIVITY   = aws_ssm_parameter.last_activity.name
      SSM_DEPLOY_ACTIVE   = aws_ssm_parameter.deploy_active.name
    }
  }

  tags = { Project = "data-pipeline" }
}

# ── EventBridge Rule (Sleep Timer) ───────────────────────────────────────────

# Runs the Sleep Lambda every 15 minutes to check if the server has been idle too long.
resource "aws_cloudwatch_event_rule" "sleep_check" {
  name                = "pipeline-sleep-check"
  description         = "Triggers the Sleep Lambda every 15 minutes to check for idle timeout"
  schedule_expression = "rate(15 minutes)"

  tags = { Project = "data-pipeline" }
}

# Tells EventBridge which Lambda to call when the timer fires.
resource "aws_cloudwatch_event_target" "sleep_lambda" {
  rule = aws_cloudwatch_event_rule.sleep_check.name
  arn  = aws_lambda_function.sleep.arn
}

# Allows EventBridge to invoke the Sleep Lambda — without this, the timer fires but gets "access denied."
resource "aws_lambda_permission" "allow_eventbridge_sleep" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sleep.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sleep_check.arn
}

# ── API Gateway (Dashboard Wake Endpoint) ────────────────────────────────────

# Public HTTP API that visitors hit — triggers the Wake Lambda to boot the server if it is asleep.
resource "aws_apigatewayv2_api" "dashboard" {
  name          = "pipeline-dashboard"
  protocol_type = "HTTP"

  tags = { Project = "data-pipeline" }
}

# Connects the API Gateway to the Wake Lambda so every incoming request runs the function.
resource "aws_apigatewayv2_integration" "wake" {
  api_id                 = aws_apigatewayv2_api.dashboard.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.wake.invoke_arn
  payload_format_version = "2.0"
}

# Catch-all route — any URL path or HTTP method triggers the Wake Lambda.
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.dashboard.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.wake.id}"
}

# Auto-deploying stage — changes go live immediately without manual "deploy API" clicks.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.dashboard.id
  name        = "$default"
  auto_deploy = true

  tags = { Project = "data-pipeline" }
}

# Allows API Gateway to invoke the Wake Lambda — without this, visitors would get "internal server error."
resource "aws_lambda_permission" "allow_apigw_wake" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.wake.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.dashboard.execution_arn}/*/*"
}
