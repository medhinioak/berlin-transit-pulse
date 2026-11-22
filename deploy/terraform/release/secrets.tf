# Postgres passwords, generated here and stored in SSM Parameter Store (standard tier, free,
# AWS-managed key aws/ssm). They are also in the Terraform state; the state bucket is private
# and encrypted (#13).

locals {
  ssm_prefix = "/btp/release"

  # One login per service schema (CLAUDE.md, Service boundaries), plus the superuser.
  pg_roles = toset(["superuser", "processor", "schedule", "metrics_jobs", "api"])
}

resource "random_password" "pg" {
  for_each = local.pg_roles

  length = 32
  # Letters and digits only, so the passwords need no escaping in connection URLs.
  special = false
}

resource "aws_ssm_parameter" "pg_password" {
  for_each = local.pg_roles

  name        = "${local.ssm_prefix}/pg/${each.key}"
  description = "Postgres password for ${each.key}"
  type        = "SecureString"
  tier        = "Standard"
  value       = random_password.pg[each.key].result
}

# Contact details for the User-Agent sent to the VBB feed (CLAUDE.md, Feed etiquette).
resource "aws_ssm_parameter" "feed_contact" {
  name        = "${local.ssm_prefix}/feed_contact"
  description = "Contact details for the VBB feed User-Agent"
  type        = "String"
  tier        = "Standard"
  value       = var.feed_contact
}

# Data bucket for the raw snapshot archive (#19). A parameter, so the bucket name with the
# account ID stays out of the repo; deploy.sh renders it as BTP_RAW_BUCKET.
resource "aws_ssm_parameter" "data_bucket" {
  name        = "${local.ssm_prefix}/data_bucket"
  description = "Data bucket for raw snapshots (ingest BTP_RAW_BUCKET)"
  type        = "String"
  tier        = "Standard"
  value       = aws_s3_bucket.data.bucket
}
