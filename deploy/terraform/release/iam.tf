# Instance role: SSM Session Manager and Run Command, reading this stack's SSM parameters,
# and writing (never deleting) raw snapshots and backups in the data bucket (s3.tf).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "server" {
  name               = "btp-release-server"
  description        = "Release EC2 instance: SSM access and its own parameters"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "server_ssm_core" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# SecureString parameters use the AWS-managed key aws/ssm, whose key policy already lets
# principals in the account decrypt through SSM, so no KMS statement is needed.
data "aws_iam_policy_document" "server_parameters" {
  statement {
    sid = "ReadReleaseParameters"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}",
      "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*",
    ]
  }
}

resource "aws_iam_role_policy" "server_parameters" {
  name   = "read-release-parameters"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.server_parameters.json
}

# Write-only access to raw/ (snapshots, #19) and backups/ (pg_dump, #28). No DeleteObject:
# raw snapshots expire through the lifecycle rule. No access to the web bucket (CD role, #18).
# s3:PutObject also covers multipart uploads.
data "aws_iam_policy_document" "server_s3" {
  statement {
    sid       = "WriteRaw"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }

  # Versioning is off, so an overwrite would lose a backup for good. PutObject and
  # CompleteMultipartUpload into backups/ must send If-None-Match: * (S3 then refuses an
  # existing key with 412). Plain `aws s3 cp` doesn't send it; use
  # `aws s3api put-object --if-none-match '*'`. The pattern follows the AWS guide
  # "Enforce conditional writes on Amazon S3 buckets".
  statement {
    sid       = "CreateBackupsOnly"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/backups/*"]

    condition {
      test     = "Null"
      variable = "s3:if-none-match"
      values   = ["false"]
    }
  }

  # CreateMultipartUpload, UploadPart and UploadPartCopy can't carry the header; the
  # upload still only lands through a CompleteMultipartUpload with If-None-Match.
  statement {
    sid       = "BackupMultipartParts"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/backups/*"]

    condition {
      test     = "Bool"
      variable = "s3:ObjectCreationOperation"
      values   = ["false"]
    }
  }

  statement {
    sid       = "ListRawAndBackups"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/*", "backups/*"]
    }
  }
}

resource "aws_iam_role_policy" "server_s3" {
  name   = "write-data-bucket"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.server_s3.json
}

resource "aws_iam_instance_profile" "server" {
  name = "btp-release-server"
  role = aws_iam_role.server.name
}
