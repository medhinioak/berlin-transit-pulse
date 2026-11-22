# AWS account

How the project's AWS account is set up and the conventions every stack follows. Set up by
hand in the console on 2026-11-18/19 (the only console work allowed; see `CLAUDE.md`).
Everything after this baseline is Terraform.

No account IDs, sign-in URLs, email addresses or credentials belong in this repository.

## Conventions

| Topic | Value |
| --- | --- |
| Region | `eu-north-1` (Europe, Stockholm) for every regional resource |
| CLI profile | `btp` |
| Tags on every resource | `Project=berlin-transit-pulse`, `Stack=release`, `Stack=poc` or `Stack=bootstrap` (state bucket), plus `ManagedBy=terraform` |
| Budget | Release stack under €20/month; alerts at $5, $15 and $20 |
| Plan | AWS Free plan with $100 in credits, from 2026-11-18 |

### Why Stockholm, not Frankfurt

The account was created through AWS's new sign-up experience. It puts every project in one of
three regions based on the contact address, and for Europe that is `eu-north-1`. A project's
region can't be changed. Changing region would need "advanced features" or a new sign-up,
which brings back AWS Organizations and, with it, the risk of losing the Free plan credits.

Nothing in this project depends on Frankfurt:
- The feed is polled twice a minute, so tens of milliseconds of extra latency don't matter.
- Visitors reach the site through CloudFront edge locations.
- The data stays in the EU.
- EC2 Graviton, S3, CloudFront, RDS, ECS and MSK are all available in `eu-north-1`.

### Why an IAM user, not IAM Identity Center

IAM Identity Center can only grant access to the account through AWS Organizations, and
creating an organization moves a Free plan account to the Paid plan. For one account and one
person, an IAM user with MFA and no access keys is enough. `aws login` gives the CLI
short-lived credentials from the console sign-in, so no long-term keys are needed. Revisit
Identity Center when the account moves to the Paid plan.

## Baseline

| Item | Setting |
| --- | --- |
| Root user | MFA on. Used only for billing and account settings |
| Admin user | One named IAM user for the maintainer (written `<admin-user>` below) with console access and MFA, in group `admins` (`AdministratorAccess` policy). **No access keys** |
| Billing preferences | Free Tier usage alerts and invoices by email |
| Budget | Monthly cost budget, **credits excluded** so alerts show real usage. Alerts at $5 and $15 (actual) and $20 (actual and forecasted) |
| Cost Anomaly Detection | Monitor for AWS services, email alert above $3 |
| IAM Access Analyzer | Account analyzer in `eu-north-1` |

## Signing in from the CLI

Needs AWS CLI v2.32.0 or later (`aws --version`).

```bash
aws login --profile btp                     # browser sign-in as <admin-user>; region eu-north-1
aws sts get-caller-identity --profile btp   # should show user/<admin-user>, not root
```

Run `aws login --profile btp` again when the session expires. The resulting `~/.aws/config`
entry looks like this (placeholders, not real values):

```ini
[profile btp]
login_session = arn:aws:iam::<account-id>:user/<admin-user>
region = eu-north-1
```

Terraform and other SDK tools pick up the same profile with `AWS_PROFILE=btp`.

## Rules

- AI coding agents (Claude Code) never run `aws` commands, `terraform apply` or
  `terraform destroy` (denied in `.claude/settings.json`). A maintainer runs them after
  reading the plan.
- No console changes beyond this baseline. Everything else is Terraform under
  `deploy/terraform/`.
- No long-lived access keys, for the admin user or anything else. GitHub Actions uses OIDC
  and the role `btp-release-deploy` (`deploy/terraform/release/github_oidc.tf`,
  [ADR-0012](adr/0012-deploy-with-terraform-github-actions-and-ssm-without-ssh.md)).

## Access for additional people

Not defined yet. Today only the maintainer has access to the account. Service work needs no
AWS access: contributors send Terraform changes with the `terraform plan` output in the pull
request and the maintainer applies them. Before an external developer needs AWS access, decide
between scoped IAM users in a group and moving to IAM Identity Center on the Paid plan (see
"Later"), and record the choice in an ADR.

## Free plan timeline

The Free plan ends six months after sign-up (around mid-May 2027) or when the credits run
out, whichever comes first. Upgrade to the Paid plan before then to keep the site live;
unused credits stay valid after upgrading. Some services may not be available on the Free
plan. Check MSK before the microservices proof-of-concept issue starts.

## Later

- **M2:** activate the `Project` cost allocation tag in Billing once the first tagged
  resource exists, so Cost Explorer can split costs by stack.
- **Paid plan:** reconsider IAM Identity Center and AWS Organizations.
