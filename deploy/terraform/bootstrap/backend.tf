# State of the bootstrap stack, in the bucket the stack itself creates (migrated after the
# first apply; deploy/terraform/README.md).
#
# The bucket name is left out on purpose (partial configuration): it contains the AWS
# account ID, which stays out of the repository. On a new machine:
#
#   terraform -chdir=deploy/terraform/bootstrap init -backend-config="bucket=btp-tfstate-<account-id>-eu-north-1"
#
# No profile here: locally AWS_PROFILE=btp selects it, CI uses OIDC credentials.
terraform {
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
