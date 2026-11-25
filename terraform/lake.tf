# The data lake bucket for agent_data (the "aws" profile): Bronze/Silver/Gold Delta tables and raw filings.
# Private, encrypted, HTTPS-only, and cheap: a few hundred MB of filings costs cents per month. The EC2 role gets
# access to this one bucket only; pods use the instance's rotating credentials, so no access keys exist anywhere.
# The deploy reads `terraform output lake_uri` and passes it to the scheduler as LAKE_URI (agent-data-config).

locals {
  # A readable, predictable name that is globally unique: project + account ID + region.
  lake_bucket_name = "data-pipeline-lake-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
}

resource "aws_s3_bucket" "lake" {
  bucket = local.lake_bucket_name

  # The lake holds the only copy of ingested history; refuse an accidental `terraform destroy` of it.
  lifecycle {
    prevent_destroy = true
  }

  tags = { Project = "data-pipeline", Purpose = "agent-data-lake" }
}

# No public access in any form, whatever a future policy or ACL might say.
resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs off: the bucket owner owns every object, and access is decided by IAM policy alone.
resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Encrypt everything at rest with S3-managed keys (free, no KMS charges).
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# No S3 versioning: Delta keeps its own table history, so versioning would only double storage costs.
# Clean up multipart uploads that never finished (a crashed upload otherwise bills quietly forever).
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Refuse any request that isn't over HTTPS.
resource "aws_s3_bucket_policy" "lake_tls_only" {
  bucket = aws_s3_bucket.lake.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.lake]
}

# The EC2 role may list, read, write and delete in the lake bucket — and nowhere else in S3.
# Delete is needed for Delta housekeeping (VACUUM) and the runner's write probe.
resource "aws_iam_role_policy" "ec2_lake_access" {
  name = "lake-bucket-access"
  role = aws_iam_role.ec2_ecr_role.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListLakeBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.lake.arn
      },
      {
        Sid      = "ReadWriteLakeObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.lake.arn}/*"
      },
    ]
  })
}
