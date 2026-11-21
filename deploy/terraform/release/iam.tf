# Instance role: SSM Session Manager and Run Command, plus reading this stack's SSM
# parameters. S3 permissions are added by #16 and #19.

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

resource "aws_iam_instance_profile" "server" {
  name = "btp-release-server"
  role = aws_iam_role.server.name
}
