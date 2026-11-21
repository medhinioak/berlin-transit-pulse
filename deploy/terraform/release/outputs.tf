output "instance_id" {
  description = "ID of the release instance (target of aws ssm start-session)."
  value       = aws_instance.server.id
}

output "public_ip" {
  description = "Elastic IP of the release instance."
  value       = aws_eip.server.public_ip
}

output "public_dns" {
  description = "Public DNS name of the Elastic IP (the CloudFront origin in #17)."
  value       = aws_eip.server.public_dns
}

output "instance_role_name" {
  description = "Name of the instance role (later stacks and issues attach S3 permissions to it)."
  value       = aws_iam_role.server.name
}

output "ssm_parameter_prefix" {
  description = "SSM Parameter Store path holding this stack's parameters."
  value       = local.ssm_prefix
}
