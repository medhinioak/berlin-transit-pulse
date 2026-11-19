# ADR-0018: Use eu-north-1 and an IAM user with `aws login` on the Free plan

- Status: Accepted
- Date: 2026-11-19
- Decider: Medhini
- Review: Claude project chat "Transport reliability tracker", 2026-11-18

## Context

The plan assumed Frankfurt (`eu-central-1`) and IAM Identity Center with SSO for CLI access.
The account was created on 2026-11-18 through AWS's new sign-up experience, on the Free plan
with $100 in credits. Two constraints came up during setup:

- The new sign-up assigns the account's project to one of three regions based on the contact
  address. For Europe that is `eu-north-1` (Stockholm). `aws login` only works in the
  project's region, and the region can't be changed.
- IAM Identity Center can only grant access to the account through AWS Organizations
  ("advanced features"). Creating an organization moves a Free plan account to the Paid plan.

## Decision

Use `eu-north-1` for every regional resource. Use an IAM user (`medhini`, group `admins`,
MFA, no access keys) for console work, and `aws login --profile btp` for short-lived CLI
credentials. Details in [`docs/aws-account.md`](../aws-account.md).

## Alternatives considered

- **Activate advanced features to choose Frankfurt and use Identity Center**: creates an
  AWS Organization, which ends the Free plan and its credits, for no benefit in a
  one-person, one-account project.
- **Sign up again with the advanced sign-up**: a second account for the same person,
  probably without Free plan credits, and a wasted day of setup.
- **IAM user with long-lived access keys**: the usual leak risk; `aws login` makes them
  unnecessary.

## Consequences

- Latency from Stockholm to the VBB feed and to Berlin users is tens of milliseconds
  higher than from Frankfurt. That doesn't matter: the feed is polled twice a minute and
  users reach the site through CloudFront.
- Every service in the plan (EC2 Graviton, S3, CloudFront, RDS, ECS, MSK) is available in
  `eu-north-1`. Prices are similar to or lower than Frankfurt; cost estimates in M2 use
  Stockholm prices.
- The CLI session expires and needs `aws login` again; no keys to rotate or leak.
- Worth revisiting when the account moves to the Paid plan (by mid-May 2027): Identity
  Center becomes possible then. The region stays, since moving running infrastructure
  isn't worth it.
