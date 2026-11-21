# Release stack

The server the whole release runs on: one EC2 t4g.small in `eu-north-1` running every
service with docker compose. Later M2 issues add S3 (#16), CloudFront (#17) and the deploy
pipeline (#18) to this stack. Decisions: [ADR-0020](../../../docs/adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md).
Conventions for all stacks: [`../README.md`](../README.md).

## What it creates

25 resources:

| File | Resources |
| --- | --- |
| `network.tf` | VPC `10.20.0.0/16`, one public subnet `10.20.1.0/24` in `eu-north-1a`, internet gateway, route table, default route, association; a security group with **no inbound rules** and all outbound |
| `iam.tf` | Instance role with `AmazonSSMManagedInstanceCore` and read access to the parameters under `/btp/release/`; instance profile |
| `ec2.tf` | t4g.small (Amazon Linux 2023, arm64), standard CPU credits, IMDSv2 with hop limit 2, 30 GB encrypted gp3 root volume, no key pair; an Elastic IP |
| `secrets.tf` | Five generated Postgres passwords as SecureString parameters `/btp/release/pg/{superuser,processor,schedule,metrics_jobs,api}`; the feed contact as `/btp/release/feed_contact` |

No NAT gateway, no load balancer, no VPC endpoints. Cost: about $18.70 (€17) a month
([`docs/cost.md`](../../../docs/cost.md)).

On first boot, `templates/cloud-init.sh.tftpl` installs Docker and git, the Docker Compose
plugin (pinned version, SHA-256 checked), Docker log rotation (10 MB × 3 files per container),
a 1 GB swap file with `vm.swappiness=10`, and creates `/opt/btp/`.

## Plan and apply (maintainer)

```bash
aws login --profile btp
export TF_VAR_feed_contact='…'   # email address or repository URL; ends up in SSM, not in git
make tf-plan STACK=release TF_STATE_BUCKET=btp-tfstate-<account-id>-eu-north-1
terraform -chdir=deploy/terraform/release apply plan.tfplan
```

`TF_STATE_BUCKET` is needed on a machine's first init only. Read the plan before applying, and
paste it into the pull request with the account ID redacted. The Terraform state contains the
Postgres passwords; it lives in the private, encrypted state bucket (#13).

## Open a shell

There is no SSH. Use SSM Session Manager (needs the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
for the AWS CLI):

```bash
aws ssm start-session --profile btp \
  --target "$(terraform -chdir=deploy/terraform/release output -raw instance_id)"
```

## Check the first boot

In the session, a few minutes after the first apply:

```bash
sudo cloud-init status --wait    # status: done
docker compose version           # Docker Compose version v5.6.1
swapon --show                    # /swapfile, 1024M
sysctl vm.swappiness             # vm.swappiness = 10
cat /etc/docker/daemon.json      # json-file, max-size 10m, max-file 3
```

If cloud-init failed, the reason is in `/var/log/cloud-init-output.log`.

## Don't replace the instance casually

Postgres and Kafka data live on the root volume, which is deleted with the instance. Until
backups exist (#28, M3), replacing the instance loses all collected data. So
`lifecycle { ignore_changes = [ami, user_data] }` stops a new Amazon Linux AMI or an edited
cloud-init script from forcing a replacement. If a plan still says
`aws_instance.server must be replaced`, stop and find out why. A deliberate rebuild is
`terraform apply -replace=aws_instance.server`, after a backup.

Changes to the cloud-init script only reach a new instance. On the running one, apply them by
hand through SSM (or through the deploy in #18).
