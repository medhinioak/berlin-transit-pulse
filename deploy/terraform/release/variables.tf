variable "aws_profile" {
  description = "AWS CLI profile used for plan and apply (see docs/aws-account.md)."
  type        = string
  default     = "btp"
}

variable "feed_contact" {
  description = "Contact for the VBB feed User-Agent (an email address or the repository URL). Pass as TF_VAR_feed_contact; it is stored in SSM, never in the repository."
  type        = string

  validation {
    condition     = length(trimspace(var.feed_contact)) > 0
    error_message = "feed_contact must not be empty: VBB asks for contact details in the User-Agent."
  }
}

variable "instance_type" {
  description = "EC2 instance type of the release server (arm64 only: images are built for arm64)."
  type        = string
  default     = "t4g.small"
}

variable "availability_zone" {
  description = "Availability zone of the public subnet and the instance."
  type        = string
  default     = "eu-north-1a"
}
