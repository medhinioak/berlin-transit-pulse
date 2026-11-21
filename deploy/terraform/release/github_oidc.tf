# GitHub Actions deploys the release without long-lived keys: the release workflow on main
# signs in through OIDC and assumes btp-release-deploy (.github/workflows/release.yml, #18).
# The role can run shell commands on the release instance only, write the web bucket and
# invalidate the distribution. Nothing else.
# Decisions and their reasons: docs/adr/0012-deploy-with-terraform-github-actions-and-ssm-without-ssh.md

locals {
  github_repository = "medhinioak/berlin-transit-pulse"
}

# With AWS provider v6 the thumbprint is optional: IAM trusts GitHub's certificate authority.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# Only workflow runs on main get a token with this subject: workflow_run and workflow_dispatch
# from main. Pull requests, other branches, tags and forks are refused.
data "aws_iam_policy_document" "deploy_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "btp-release-deploy"
  description          = "GitHub Actions release workflow: deploy through SSM, upload the page"
  assume_role_policy   = data.aws_iam_policy_document.deploy_assume_role.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  # The document and the instance are separate statements: the AWS-owned document has no tags,
  # so a tag condition on it would deny every SendCommand.
  statement {
    sid       = "RunShellScriptDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}::document/AWS-RunShellScript"]
  }

  statement {
    sid       = "RunOnReleaseInstance"
    actions   = ["ssm:SendCommand"]
    resources = [aws_instance.server.arn]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Project"
      values   = ["berlin-transit-pulse"]
    }

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Stack"
      values   = ["release"]
    }
  }

  # These two actions don't support resource-level permissions.
  statement {
    sid = "ReadCommandResults"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ListWebBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.web.arn]
  }

  statement {
    sid = "WriteWebBucket"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.web.arn}/*"]
  }

  statement {
    sid       = "InvalidateDistribution"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.main.arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-release"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
