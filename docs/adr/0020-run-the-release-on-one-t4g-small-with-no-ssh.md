# ADR-0020: Run the release on one t4g.small with no SSH

- Status: Accepted
- Date: 2026-11-21
- Decider: Maintainer (@medhinioak)
- Review: decisions fixed in issue #15 before implementation; implementation reviewed in the pull request that closes #15, 2026-11-21

## Context

The release runs 24/7 under a €20 monthly ceiling ([cost](../cost.md)), on one host with
docker compose ([architecture](../architecture.md#deployment-targets)). The team is one
part-time maintainer, so the server has to be rebuildable from code, reachable without
managing keys, and safe from surprise bills. Stockholm on-demand prices (2026-11-21): t4g.small
$0.0172 per hour (about $12.56 a month), surplus CPU credits $0.04 per vCPU-hour. A t4g.small
has 2 vCPUs, 2 GB of RAM and a CPU-credit baseline of 20% per vCPU. Postgres and Kafka keep
their data on the instance's disk, and there are no backups until #28 (M3).

## Decision

Run the release on one EC2 t4g.small (arm64, Amazon Linux 2023) in a public subnet, with:

- **Standard CPU credits.** When the credit balance runs out, the instance is throttled to its
  baseline instead of buying surplus credits.
- **IMDSv2 only, hop limit 2.** Session tokens are required; the hop limit of 2 lets
  containers on the Docker bridge network reach the metadata service and use the instance role.
- **SSM Session Manager instead of SSH.** No key pair, no inbound rule for port 22 (no inbound
  rules at all until #17 adds port 80 for CloudFront). Shell access and deploys go through SSM
  with short-lived IAM credentials.
- **AWS's default reverse DNS name** (`ec2-<ip>.eu-north-1.compute.amazonaws.com`) on the
  Elastic IP.
- **Data on the root volume, protected from accidental replacement.** One 30 GB encrypted gp3
  root volume; Terraform ignores changes to the AMI and the cloud-init script, so neither forces
  a new instance.
- **1 GB swap with `vm.swappiness=10`**, as a margin against out-of-memory kills, not as working
  memory.

## Alternatives considered

- **Unlimited CPU credits** (the t4g default): no throttling, but sustained load is billed at
  $0.04 per vCPU-hour. A busy month at full speed on both vCPUs could add about $47, more than
  twice the ceiling. Throttling is visible in CloudWatch and cheaper to fix (size up in #20).
- **IMDSv2 with hop limit 1** (AWS's default for new instances): containers can't reach the
  metadata service, so services that need AWS (raw snapshots to S3, #19) would need access keys.
- **IMDSv1 allowed**: open to SSRF credential theft; no reason to allow it.
- **SSH with a key pair**: a key to keep safe and rotate, and an open port to watch. SSM needs
  only outbound HTTPS and logs sessions in CloudTrail.
- **A custom PTR record**: needs a domain we own with an A record pointing at the Elastic IP.
  There is no domain yet ([parking lot](../parking-lot.md)). VBB sees the contact details in the
  User-Agent, which is what identifies the client.
- **A separate EBS data volume** that survives instance replacement: more Terraform, mounts in
  cloud-init and more moving parts before there is a measured need. Revisit after the rebuild
  drill in M5.
- **A larger instance (t4g.medium, about +$12.50 a month in Stockholm)**: decided from
  measurements on the server in #20, not up front.

## Consequences

- The baseline cost is fixed: no surplus-credit charges can appear on the bill. The price is
  that a long CPU-heavy period (a GTFS import, a backfill) runs slower once credits are spent.
- Nothing listens on a public port in this stack. Getting a shell needs `aws login` and the
  Session Manager plugin; there is no break-glass SSH path, so a broken SSM agent means
  rebuilding the instance.
- Any container on the host can use the instance role, so the role must stay narrow: today SSM
  and read access to `/btp/release/*` only.
- Replacing the instance loses all Postgres and Kafka data until backups exist (#28). A new AMI
  or a cloud-init change does not reach the running instance by itself: updates are applied
  through SSM, and a rebuild is a deliberate `terraform apply -replace=aws_instance.server`.
- Mail servers and some services treat generic `compute.amazonaws.com` names as low reputation.
  The server sends no mail, so this only matters if a domain is added later.
- Worth revisiting when #20 shows the instance is throttled or short of memory, when backups
  (#28) make replacement safe, or when the project gets a domain.
