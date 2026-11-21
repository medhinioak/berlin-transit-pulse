# State of the release stack, in the bucket created by the bootstrap stack
# (deploy/terraform/README.md).
#
# The bucket name is left out on purpose (partial configuration): it contains the AWS
# account ID, which stays out of the repository. On a machine's first init:
#
#   make tf-plan STACK=release TF_STATE_BUCKET=btp-tfstate-<account-id>-eu-north-1
#
# No profile here: locally AWS_PROFILE=btp selects it, CI uses OIDC credentials.
terraform {
  backend "s3" {
    key          = "release/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
