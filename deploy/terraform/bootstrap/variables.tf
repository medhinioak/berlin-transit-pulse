variable "aws_profile" {
  description = "AWS CLI profile used for plan and apply (see docs/aws-account.md)."
  type        = string
  default     = "btp"
}
