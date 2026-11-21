output "instance_id" {
  description = "ID of the release instance (aws ssm start-session; GitHub secret RELEASE_INSTANCE_ID)."
  value       = aws_instance.server.id
}

output "public_ip" {
  description = "Elastic IP of the release instance."
  value       = aws_eip.server.public_ip
}

output "public_dns" {
  description = "Public DNS name of the Elastic IP (the CloudFront origin of /api/*)."
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

output "data_bucket_name" {
  description = "Name of the data bucket (raw/vbb-rt/, history/, backups/)."
  value       = aws_s3_bucket.data.bucket
}

output "web_bucket_name" {
  description = "Name of the web bucket holding the static site (GitHub secret WEB_BUCKET)."
  value       = aws_s3_bucket.web.bucket
}

output "web_bucket_arn" {
  description = "ARN of the web bucket."
  value       = aws_s3_bucket.web.arn
}

output "web_bucket_regional_domain_name" {
  description = "Regional domain name of the web bucket (the CloudFront origin of the page)."
  value       = aws_s3_bucket.web.bucket_regional_domain_name
}

output "cloudfront_domain_name" {
  description = "The site's public domain, https://<this>/ (GitHub secret CLOUDFRONT_DOMAIN)."
  value       = aws_cloudfront_distribution.main.domain_name
}

output "cloudfront_distribution_id" {
  description = "ID of the CloudFront distribution (GitHub secret CLOUDFRONT_DISTRIBUTION_ID)."
  value       = aws_cloudfront_distribution.main.id
}

output "cloudfront_distribution_arn" {
  description = "ARN of the CloudFront distribution (the deploy role may invalidate it)."
  value       = aws_cloudfront_distribution.main.arn
}

output "deploy_role_arn" {
  description = "ARN of the role the release workflow assumes through OIDC (GitHub secret AWS_DEPLOY_ROLE_ARN)."
  value       = aws_iam_role.deploy.arn
}
