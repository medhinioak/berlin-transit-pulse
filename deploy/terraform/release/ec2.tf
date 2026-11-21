# The release server: one t4g.small running the whole release with docker compose.
# Decisions and their reasons: docs/adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md

locals {
  # Docker Compose plugin for arm64, pinned and checked against the SHA-256 published with the
  # release (https://github.com/docker/compose/releases). Bump both together.
  compose_version = "v5.6.1"
  compose_sha256  = "42cfa75f51ad0ccf290bad37899e0038a8c10a0069e85233ecc1d53e3ff1347d"
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type          = var.instance_type
  availability_zone      = var.availability_zone
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.server.name

  # No key pair and no port 22: shell access is through SSM Session Manager only.

  # The root volume holds all collected data: refuse termination from the console or API.
  # A deliberate rebuild sets this to false in a reviewed change first.
  disable_api_termination = true

  # Throttle to the baseline when credits run out instead of paying for surplus credits.
  credit_specification {
    cpu_credits = "standard"
  }

  monitoring = false

  # IMDSv2 only. Hop limit 2, because a container is one network hop further away than the
  # host: with 1, containers can't reach the metadata service and can't use the instance role.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  # Postgres and Kafka data live on this volume (no separate data volume yet).
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 30
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/cloud-init.sh.tftpl", {
    compose_version = local.compose_version
    compose_sha256  = local.compose_sha256
  })

  tags = {
    Name = "btp-release"
  }

  # Replacing the instance deletes its root volume, and with it all Postgres and Kafka data,
  # until backups exist (#28, M3). A newer AMI from the SSM parameter would force exactly that.
  # A changed cloud-init script would only stop and start the instance, and cloud-init does not
  # run it again anyway, so the change is ignored too: apply it on the running server through
  # SSM. Rebuild on purpose with `terraform apply -replace=aws_instance.server`.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  # The instance has no public address until the Elastic IP is attached, and cloud-init waits
  # for the network (templates/cloud-init.sh.tftpl); the route must exist by then.
  depends_on = [aws_route.public_internet, aws_route_table_association.public]
}

# Stable public address: the CloudFront origin (#17) and the source address VBB sees.
resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id

  tags = {
    Name = "btp-release"
  }
}

# Reverse DNS: AWS's default PTR record (ec2-<ip>.eu-north-1.compute.amazonaws.com) is kept.
# A custom PTR needs a domain we own with an A record pointing at the Elastic IP, and the
# project has no domain yet (custom domain: docs/parking-lot.md). With one, this would be:
#
# resource "aws_eip_domain_name" "server" {
#   allocation_id = aws_eip.server.allocation_id
#   domain_name   = "origin.example.org"
# }
