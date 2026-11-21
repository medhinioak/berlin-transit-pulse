provider "aws" {
  region  = "eu-north-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "berlin-transit-pulse"
      Stack     = "release"
      ManagedBy = "terraform"
    }
  }
}
