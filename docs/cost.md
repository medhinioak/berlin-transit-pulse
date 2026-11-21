# Cost model and controls

What the release stack costs, how spend is kept under the ceiling and what happens when it is
no longer affordable. The release must stay under **€20 per month**, shown from Cost Explorer
([definition of done](spec.md#definition-of-done-for-v10)). A change that adds monthly cost
states the estimated € difference in its pull request.

## Release baseline

The baseline needs no AWS credits. Prices are on-demand, for Stockholm (`eu-north-1`,
[ADR-0018](adr/0018-use-eu-north-1-and-an-iam-user-with-aws-login.md)). The first three rows
were checked on 2026-11-21 against the AWS public price list (EC2 and VPC offer files for
`eu-north-1`, published that day). Rows marked `~` are estimates that have not been re-priced
for Stockholm yet; they are small and are checked in the M5 cost review (#42).

| Item | Sizing | USD per month |
| --- | --- | ---: |
| EC2 t4g.small | $0.0172 per hour × 730 h, standard CPU credits ([ADR-0020](adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md)) | 12.56 |
| Public IPv4 (Elastic IP) | $0.005 per hour × 730 h | 3.65 |
| EBS gp3 disk | 30 GB × $0.0836 per GB-month (Kafka 24 h retention, Postgres 30 days, swap); baseline IOPS and throughput are included | 2.51 |
| S3 raw snapshots | 1 snapshot every 5 min × 2.3 MB, deleted after 14 days (about 9 GB stored) | ~0.25 |
| S3 Parquet history and backups | Grows about 1–3 GB per month | ~0.10 |
| CloudFront, CloudWatch (10 custom metrics), SNS email | Within the always-free allowances | 0.00 |
| Route 53 hosted zone (optional, plus about €10–15 per year for a domain) | 1 zone | 0.50 |
| **Total** | | **~19.57** |

That is roughly €17.80 (at $1 = €0.91, 2026-11-20), under the ceiling. The server alone (the
first three rows, the release stack from #15) is $18.72, about €17.00. Data transfer out is
not in the table: CloudFront serves the site, and the server's own traffic out is small.

## Keeping spend low

- **Never store every snapshot.** At 30 s that would be about 200 GB a month. Store raw
  snapshots every 5 minutes with a 14-day lifecycle rule and keep the compact Parquet events.
- **Short retention in hot stores:** Kafka 24 h; Postgres 30 days of detailed events, then
  Parquet only.
- **Serve everything through CloudFront** with cache headers, so traffic spikes do not hit
  the server.
- **Cheap building blocks:** arm64 (Graviton) instances, no NAT gateway, no load balancer, no
  managed Kafka in the release stack.
- **Budgets and anomaly detection** are set up in the account baseline: alerts at $5 and $15
  (actual) and $20 (actual and forecast), with credits excluded so alerts show real usage, and
  a Cost Anomaly Detection alert above $3 ([`aws-account.md`](aws-account.md)).
- **If it gets tight:** a 1-year Compute Savings Plan (about 30% off compute), or a spot
  instance (about $0.0087 per hour, roughly $6.40 per month, but it can be interrupted).

## Credits and the Free plan

The account is on the AWS Free plan with $100 in credits from 2026-11-18. The plan ends six
months after sign-up (around mid-May 2027) or when the credits run out; upgrade to the Paid
plan before then to keep the site live. The baseline above does not rely on credits.

If credits are used anyway, one option is RDS for PostgreSQL (smallest size, about $13–15 per
month) for about 5 months, then a move back to Postgres on the server when the credits end.
Credits are not spent on MSK. That choice would get its own ADR (planned ADR-0006).

## Microservices proof of concept (one-off)

About $1 per hour while it is up, so a 4–6 hour run costs about $4–6 (estimates; price it in
the AWS calculator before applying). Most of that is MSK Serverless at $0.75 per cluster-hour,
the rest is Fargate tasks, the smallest RDS instance and an Application Load Balancer. Public
subnets avoid a NAT gateway.

Safety nets: a budget alert just for the run, a separate Terraform state and tags, and a
scheduled GitHub Actions workflow that runs `terraform destroy` in case it is forgotten.
Some services may not be available on the Free plan: check MSK before the proof-of-concept
issue (#48) starts. See the [alternatives track](alternatives.md).

## Shutdown and archive plan

The exit plan when the live service is no longer affordable:

1. Run a final Parquet export.
2. Rebuild the site as static pages of historical findings on S3 and CloudFront (about
   $0–1 per month).
3. Destroy the compute with Terraform.
4. Mark the README as archived and keep the CC-BY attribution.

`terraform apply` brings the live service back. Planned ADR-0013 records this together with
the cost ceiling.

## Open items

- The $20 budget alert sits just above the baseline estimate of about $19.60, so ordinary
  variation (data transfer, a busier month for S3) can trip it. The alerts are in USD and the
  ceiling is in EUR; check against the real bill in #21 before moving the thresholds.
- Retention of the nightly `pg_dump` backups is not decided (#28).
