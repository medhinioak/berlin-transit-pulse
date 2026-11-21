# S3 buckets of the release stack: a data bucket for raw feed snapshots, Parquet history and
# backups, and a web bucket for the static site that CloudFront serves (cloudfront.tf).
# Both are private, SSE-S3 encrypted, TLS-only, with object ownership enforced (no ACLs).
# Versioning stays off: object keys are unique and the instance role cannot delete.

locals {
  data_bucket_name = "btp-data-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"
  web_bucket_name  = "btp-web-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"
}

# --- Data bucket: raw/vbb-rt/ (#19), history/ (M3/M4), backups/ (#28) ---

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket_name

  # Holds backups and the Parquet history, the data that outlives the server.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "data_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.data_bucket.json

  # S3 rejects a bucket policy while the public access block is still being applied.
  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # One snapshot every 5 minutes would otherwise grow without bound (docs/cost.md).
  # S3 rounds expiry up to the next midnight UTC, so objects live 14 to 15 days.
  rule {
    id     = "expire-raw-snapshots"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = 14
    }
  }

  # The instance cannot abort uploads itself (no s3:AbortMultipartUpload); this cleans up.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# --- Web bucket: the static site. Not a website endpoint; CloudFront reads it through
# Origin Access Control (cloudfront.tf). The CD role (#18) writes it. ---

resource "aws_s3_bucket" "web" {
  bucket = local.web_bucket_name
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket = aws_s3_bucket.web.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# TLS only, and reads by this stack's CloudFront distribution through OAC (cloudfront.tf).
# CloudFront fetches from an S3 origin with the viewer's protocol, so the distribution must
# use viewer_protocol_policy redirect-to-https or https-only: with allow-all, HTTP viewers
# get 403 from the TLS-only statement below.
# Without s3:ListBucket, CloudFront gets 403 (not 404) for a missing key.
data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.web.arn,
      "${aws_s3_bucket.web.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "AllowCloudFrontOAC"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.main.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json

  # S3 rejects a bucket policy while the public access block is still being applied.
  depends_on = [aws_s3_bucket_public_access_block.web]
}
