# Release stack

The server the whole release runs on: one EC2 t4g.small in `eu-north-1` running every
service with docker compose, plus the S3 buckets for data and the static site (#16) and the
CloudFront distribution that serves the site (#17), and the OIDC role GitHub Actions deploys
with (#18). Decisions: [ADR-0020](../../../docs/adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md) (server),
[ADR-0021](../../../docs/adr/0021-serve-the-site-through-cloudfront-with-an-s3-origin-and-a-cloudfront-only-ec2-origin.md) (CloudFront),
[ADR-0012](../../../docs/adr/0012-deploy-with-terraform-github-actions-and-ssm-without-ssh.md) (deploys).
Operating the server: [`docs/runbook.md`](../../../docs/runbook.md).
Conventions for all stacks: [`../README.md`](../README.md).

## What it creates

45 resources:

| File | Resources |
| --- | --- |
| `network.tf` | VPC `10.20.0.0/16`, one public subnet `10.20.1.0/24` in `eu-north-1a`, internet gateway, route table, default route, association; a security group with all outbound and no inbound rules of its own (`cloudfront.tf` adds port 80 from CloudFront) |
| `iam.tf` | Instance role with `AmazonSSMManagedInstanceCore`, read access to the parameters under `/btp/release/` and write-only access to the data bucket's `raw/` and `backups/`; instance profile |
| `ec2.tf` | t4g.small (Amazon Linux 2023, arm64), standard CPU credits, IMDSv2 with hop limit 2, 30 GB encrypted gp3 root volume, no key pair; an Elastic IP |
| `s3.tf` | Data bucket `btp-data-<account-id>-eu-north-1` with lifecycle rules, web bucket `btp-web-<account-id>-eu-north-1` with a rule aborting incomplete uploads; each with public access block, `BucketOwnerEnforced`, SSE-S3 and a TLS-only bucket policy; the web bucket's policy also lets the distribution read objects |
| `cloudfront.tf` | Distribution with an S3 origin (Origin Access Control) and the instance as `/api/*` origin, a response headers policy for the page, and the security group rule for port 80 from CloudFront's origin-facing prefix list |
| `github_oidc.tf` | GitHub Actions OIDC provider; role `btp-release-deploy`, trusted only for workflow runs on `main`, that can send `AWS-RunShellScript` commands to the release instance, read their results, write the web bucket and invalidate the distribution |
| `secrets.tf` | Five generated Postgres passwords as SecureString parameters `/btp/release/pg/{superuser,processor,schedule,metrics_jobs,api}`; the feed contact as `/btp/release/feed_contact`; the data bucket name as `/btp/release/data_bucket` (ingest's `BTP_RAW_BUCKET`) |

No NAT gateway, no load balancer, no VPC endpoints. Cost: about $18.72 (€17.00) a month for
the server, plus about $0.35 (€0.32) for S3 once raw snapshots are uploaded (#19), about
$19.07 (€17.35) in all. The buckets cost nothing while empty, and CloudFront stays within its
always-free allowance (1 TB and 10 million requests a month)
([`docs/cost.md`](../../../docs/cost.md)).

On first boot, `templates/cloud-init.sh.tftpl` installs Docker and git, the Docker Compose
plugin (pinned version, SHA-256 checked), Docker log rotation (10 MB × 3 files per container),
a 1 GB swap file with `vm.swappiness=10`, and creates `/opt/btp/`.

## Buckets

Both buckets are private (all four public access blocks), encrypted with SSE-S3, accept only
TLS requests and have object ownership enforced (no ACLs). Versioning is off: the instance
cannot delete, and it cannot overwrite a backup (see below). The account ID in the names comes from the caller identity.

| Bucket | Prefix | Content | Retention | Written by |
| --- | --- | --- | --- | --- |
| `btp-data-…` | `raw/vbb-rt/` | Raw feed snapshots, one every 5 minutes (#19) | 14 days (S3 runs expiry at midnight UTC, so 14–15 days) | Instance role |
| `btp-data-…` | `history/` | Parquet stop events (M3/M4) | Kept | Not decided yet |
| `btp-data-…` | `backups/` | Nightly `pg_dump` (#28) | Kept for now; #28 decides retention | Instance role |
| `btp-web-…` | | Static site; CloudFront reads it through Origin Access Control (#17), not a website endpoint | Replaced on deploy | CD role (#18) |

Incomplete multipart uploads in both buckets are aborted after 1 day. The instance role
has `s3:PutObject` on `raw/*` and `backups/*` and `s3:ListBucket` limited to those prefixes:
no `DeleteObject`, nothing on `history/` or the web bucket. Listing needs the trailing slash
(`aws s3 ls s3://$BUCKET/backups/`); `…/backups` or the bucket root is AccessDenied.

Writes to `backups/` must create a new key: `PutObject` and `CompleteMultipartUpload` need the
`If-None-Match: *` header, and S3 answers an existing key with `412 Precondition Failed`.
Plain `aws s3 cp` doesn't send the header, so upload backups with
`aws s3api put-object --if-none-match '*'` (single requests up to 5 GB). `raw/` has no such
guard; a reused key there is overwritten and expires anyway. The data bucket has
`prevent_destroy`, because it holds the data that outlives the server; removing it needs a
reviewed change first.

After the first apply, check the permissions from an SSM session on the instance (see
[Open a shell](#open-a-shell)). `aws ssm start-session` needs the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
installed on your machine, next to the AWS CLI; without it the session doesn't open
(`SessionManagerPlugin is not found`).

```bash
BUCKET=btp-data-<account-id>-eu-north-1
echo test > /tmp/check.txt
aws s3 cp /tmp/check.txt "s3://$BUCKET/raw/_check/check.txt"      # upload: works
aws s3 rm "s3://$BUCKET/raw/_check/check.txt"                     # AccessDenied (expected)
aws s3 cp /tmp/check.txt "s3://$BUCKET/backups/_check.txt"        # AccessDenied (no If-None-Match)
aws s3api put-object --if-none-match '*' --bucket "$BUCKET" \
  --key backups/_check.txt --body /tmp/check.txt                  # works
aws s3api put-object --if-none-match '*' --bucket "$BUCKET" \
  --key backups/_check.txt --body /tmp/check.txt                  # PreconditionFailed (412)
```

The `raw/_check/` object sits outside `raw/vbb-rt/`, so snapshot readers never see it, and it
expires through the lifecycle rule. `backups/` has no expiry: delete `backups/_check.txt`
afterwards from your own machine with `aws s3 rm --profile btp`.

## CloudFront

The site's public URL is `https://<cloudfront_domain_name>/` (the default `*.cloudfront.net`
domain and certificate, `PriceClass_100`, HTTP/2 and HTTP/3). HTTP redirects to HTTPS.

| Path | Origin | Cache | Response headers |
| --- | --- | --- | --- |
| `/api/*` | Instance's public DNS name, HTTP on port 80, to nginx, then metrics-api. 10 s connect and read timeouts | `UseOriginCacheControlHeaders-QueryStrings`: as long as the API's `Cache-Control` says (`max-age=30`), keyed on the query string. All viewer headers except `Host` are forwarded. GET and HEAD only (no CORS: the page is same-origin) | Managed `SecurityHeadersPolicy` |
| everything else | Web bucket through Origin Access Control; `index.html` is the root object | `CachingOptimized`: up to a day when the object has no `Cache-Control` | `btp-release-web`: nginx's CSP, `Referrer-Policy: no-referrer`, HSTS, nosniff and `X-Frame-Options: DENY` |

- **Origin protection.** Port 80 on the instance accepts only CloudFront's origin-facing
  ranges (managed prefix list `com.amazonaws.global.cloudfront.origin-facing`); nothing else is
  inbound. There is no secret header, so any CloudFront distribution could reach the origin; the
  API is public and read-only anyway (parking lot).
- **Web bucket stays private.** Only this distribution may `s3:GetObject`. Without
  `s3:ListBucket`, a missing key answers 403, not 404.
- **The CSP lives in two places.** `deploy/compose/web/nginx.conf` locally and the
  `btp-release-web` policy in `cloudfront.tf` here. Change them together.
- **TLS.** With the default certificate CloudFront allows TLS 1.0 and up (`TLSv1` policy).
  Requiring TLS 1.2 needs a custom domain with its own certificate.
- **Caching after a deploy.** The deploy uploads `index.html` with `Cache-Control: no-cache` and
  the assets with `max-age=300`, as nginx does locally, and invalidates `/*`.
- No access logs (cost).

A new distribution takes about 5–15 minutes to deploy; `terraform apply` waits for it. Until
the first deploy uploads the page and starts nginx on port 80, check only:

```bash
DOMAIN=$(terraform -chdir=deploy/terraform/release output -raw cloudfront_domain_name)
aws cloudfront get-distribution --profile btp \
  --id "$(terraform -chdir=deploy/terraform/release output -raw cloudfront_distribution_id)" \
  --query Distribution.Status                     # "Deployed"
curl -sI "https://$DOMAIN/"                       # 403 from S3 (empty bucket), not a TLS error;
                                                  # content-security-policy header present
curl -sI "http://$DOMAIN/"                        # 301 to https://
```

`/api/*` answers 502 or 504 until nginx listens on port 80. After that, the release workflow's
`smoke` job checks the whole path on every deploy.

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

## Deploy setup (maintainer, once)

GitHub Actions deploys through the role in `github_oidc.tf` ([runbook](../../../docs/runbook.md)).
After the apply, add these repository secrets (Settings → Secrets and variables → Actions).
They are secrets rather than variables, so the values are masked in the public logs:

| Secret | Output |
| --- | --- |
| `AWS_DEPLOY_ROLE_ARN` | `deploy_role_arn` |
| `RELEASE_INSTANCE_ID` | `instance_id` |
| `WEB_BUCKET` | `web_bucket_name` |
| `CLOUDFRONT_DISTRIBUTION_ID` | `cloudfront_distribution_id` |
| `CLOUDFRONT_DOMAIN` | `cloudfront_domain_name` |

```bash
for o in deploy_role_arn instance_id web_bucket_name cloudfront_distribution_id cloudfront_domain_name; do
  echo "$o=$(terraform -chdir=deploy/terraform/release output -raw "$o")"
done
```

The first workflow run creates the `btp-*` packages in GHCR as private, so the server can't
pull them and that deploy fails. Make each package public (the package's Package settings →
Change visibility), then re-run the workflow.

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
`lifecycle { ignore_changes = [ami, user_data] }` stops a new Amazon Linux AMI from forcing a
replacement, and `disable_api_termination` stops termination from the console or the API. If a
plan still says `aws_instance.server must be replaced`, stop and find out why: other attributes
(the availability zone, the subnet) still force one. A deliberate rebuild, after a backup: set
`disable_api_termination = false` in a reviewed change and apply it, then
`terraform apply -replace=aws_instance.server`.

Changes to the cloud-init script (a new Compose version, say) show no diff in the plan and only
reach a new instance. On the running one, apply them by hand through SSM (or through the deploy
in #18).
