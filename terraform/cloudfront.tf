# CloudFront + S3 failover for the dashboard.
#
# Normal operation: CloudFront proxies every request to the EC2 instance (port 32147).
# During a spot replacement (instance down): CloudFront fails over to an S3 bucket
# that serves a static "switching servers" loading page.
#
# Cost: $0/month — free tier covers 1 TB transfer + 10M requests.
# Caching: fully disabled (CachingDisabled managed policy) so CloudFront acts as
# a transparent reverse proxy with automatic failover.

# ── S3 Bucket — holds the static loading page ────────────────────────────────

resource "aws_s3_bucket" "loading_page" {
  bucket = "pipeline-loading-page-${data.aws_caller_identity.current.account_id}"

  tags = { Project = "data-pipeline" }
}

# Block all public access — CloudFront reads via OAC, not public URLs
resource "aws_s3_bucket_public_access_block" "loading_page" {
  bucket = aws_s3_bucket.loading_page.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload the loading page HTML to S3
resource "aws_s3_object" "loading_page_html" {
  bucket       = aws_s3_bucket.loading_page.id
  key          = "index.html"
  source       = "${path.module}/loading-page.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/loading-page.html")
}

# ── CloudFront Origin Access Control — secure S3 reads ────────────────────────

resource "aws_cloudfront_origin_access_control" "loading_page" {
  name                              = "pipeline-loading-page-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# S3 bucket policy — allow only CloudFront (via OAC) to read objects
resource "aws_s3_bucket_policy" "loading_page" {
  bucket = aws_s3_bucket.loading_page.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.loading_page.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.dashboard.arn
        }
      }
    }]
  })
}

# ── CloudFront Distribution ──────────────────────────────────────────────────

resource "aws_cloudfront_distribution" "dashboard" {
  enabled      = true
  comment      = "Dashboard reverse proxy with S3 failover during spot replacement"
  price_class  = "PriceClass_100" # US + Europe edges only — cheapest tier
  http_version = "http2and3"

  # Primary origin — EC2 instance on port 32147
  origin {
    domain_name = aws_eip.pipeline_eip.public_dns  # CloudFront requires a hostname, not a raw IP
    origin_id   = "ec2-dashboard"

    custom_origin_config {
      http_port                = 32147
      https_port               = 443
      origin_protocol_policy   = "http-only" # dashboard runs plain HTTP behind the EIP
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # seconds — long enough for a cold Snowflake warehouse start (~30-60s)
      origin_keepalive_timeout = 5
    }
  }

  # Failover origin — S3 loading page
  origin {
    domain_name              = aws_s3_bucket.loading_page.bucket_regional_domain_name
    origin_id                = "s3-loading-page"
    origin_access_control_id = aws_cloudfront_origin_access_control.loading_page.id
  }

  # Origin group — try EC2 first; on failure, serve the S3 loading page
  origin_group {
    origin_id = "dashboard-failover"

    failover_criteria {
      status_codes = [500, 502, 503, 504]
    }

    member {
      origin_id = "ec2-dashboard"
    }

    member {
      origin_id = "s3-loading-page"
    }
  }

  # Dash callbacks and assets — direct to EC2 with POST allowed.
  # Pattern /*_dash-* matches any prefix (/_dash-*, /dashboard/_dash-*, /weather/_dash-*)
  # because Dash registers routes under its url_base_pathname (e.g. /dashboard/).
  # Without this, POST callbacks fall through to the default behavior which only allows GET.
  ordered_cache_behavior {
    path_pattern           = "/*_dash-*"
    target_origin_id       = "ec2-dashboard"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]

    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader
  }

  # Everything else (page loads, assets) — origin group with S3 failover
  default_cache_behavior {
    target_origin_id       = "dashboard-failover"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]

    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader
  }

  # Custom error response — connection timeouts serve the S3 loading page
  custom_error_response {
    error_code            = 502
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 5 # cache error response for only 5 seconds
  }

  custom_error_response {
    error_code            = 503
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 5
  }

  custom_error_response {
    error_code            = 504
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 5
  }

  # S3 returns 403 (not 404) for missing keys when s3:ListBucket is not granted.
  # This happens during spot replacement: EC2 is down, S3 failover serves the request,
  # but the path (e.g. /dashboard/) has no matching S3 object → 403 Access Denied XML.
  # Without this handler, visitors see raw XML instead of the loading page.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 5
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default CloudFront certificate (*.cloudfront.net) — free, no custom domain needed
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Project = "data-pipeline" }
}
