# One VPC with one public subnet. No NAT gateway, no IPv6 and no VPC endpoints: they cost
# money and the single instance reaches the internet directly through its Elastic IP.

resource "aws_vpc" "main" {
  cidr_block = "10.20.0.0/16"

  # Needed for the instance's public DNS name, the CloudFront origin of /api/* (cloudfront.tf).
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "btp-release"
  }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.1.0/24"
  availability_zone = var.availability_zone

  # The instance gets its public address from the Elastic IP, not from the subnet.
  map_public_ip_on_launch = false

  tags = {
    Name = "btp-release-public"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "btp-release"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "btp-release-public"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The only inbound rule is port 80 from CloudFront's origin-facing prefix list (cloudfront.tf).
# Shell access is through SSM Session Manager, which needs only outbound HTTPS.
# The description below is out of date but stays: changing it replaces the security group.
resource "aws_security_group" "server" {
  name        = "btp-release-server"
  description = "Release server: no inbound, all outbound"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "btp-release-server"
  }
}

# Outbound to the VBB feed, GHCR, S3 and SSM.
resource "aws_vpc_security_group_egress_rule" "server_all" {
  security_group_id = aws_security_group.server.id
  description       = "All outbound IPv4"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
