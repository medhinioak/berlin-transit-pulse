output "state_bucket_name" {
  description = "Name of the Terraform state bucket (pass to init as -backend-config=\"bucket=...\")."
  value       = aws_s3_bucket.state.bucket
}

output "state_bucket_arn" {
  description = "ARN of the Terraform state bucket."
  value       = aws_s3_bucket.state.arn
}
