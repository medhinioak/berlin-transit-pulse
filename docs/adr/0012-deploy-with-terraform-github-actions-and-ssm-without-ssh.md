# ADR-0012: Deploy with Terraform, GitHub Actions and SSM, without SSH

- Status: Accepted
- Date: 2026-11-21
- Decider: Maintainer (@medhinioak)
- Review: decisions fixed in issue #18 before implementation; implementation reviewed in the pull request that closes #18, 2026-11-21

## Context

The release runs as docker compose on one t4g.small (arm64, 2 GB RAM) with no inbound port
but 80 from CloudFront and no SSH
([ADR-0020](0020-run-the-release-on-one-t4g-small-with-no-ssh.md),
[ADR-0021](0021-serve-the-site-through-cloudfront-with-an-s3-origin-and-a-cloudfront-only-ec2-origin.md)).
M2 needs every merge to `main` to reach that server and the S3-hosted page without a manual
step. One part-time maintainer looks after it, so a deploy that fails has to fail visibly, and
going back has to be one action. The repository and its CI logs are public. Building the five
Python images on the server would compete with Kafka and Postgres for its 2 GB and its CPU
credits. CLAUDE.md rules out long-lived AWS keys, and the release must stay under €20 a month.

## Decision

GitHub Actions deploys, Terraform defines everything it may touch, and SSM Run Command is the
only way onto the server:

- **AWS sign-in through GitHub OIDC.** The release stack has the GitHub OIDC provider and the role
  `btp-release-deploy`, trusted only for tokens with `sub =
  repo:medhinioak@36889960/berlin-transit-pulse@1462859808:ref:refs/heads/main`. The repository
  uses GitHub's immutable OIDC subject, which adds the owner and repository IDs, so a recreated
  repository with the same name can't assume the role. The role can do four things:
  - `ssm:SendCommand` with `AWS-RunShellScript` on the release instance (tag conditions on
    `Project` and `Stack`);
  - read the command results;
  - write and delete objects in the web bucket;
  - invalidate the distribution.

  Account-specific IDs are repository secrets, so they are masked in public logs.
- **Images built on native arm64 runners** (`ubuntu-24.04-arm`), one job per service, pushed to
  GHCR as `ghcr.io/medhinioak/btp-<service>:<sha>` and `:main` with `GITHUB_TOKEN`. CI keeps
  building amd64 for its tests.
- **Public GHCR packages.** The server pulls without registry credentials. The images contain
  only the open-source code of this public repository.
- **Pull and restart.** One SSM command checks out the SHA in `/opt/btp/repo` and runs
  `deploy/release/deploy.sh`. The script renders the env file from Parameter Store, then runs
  `docker compose pull` and `up -d --no-build`, and waits as `make up` does. Containers
  restart in place: a minute or two of downtime per deploy. A failed static GTFS import
  doesn't fail the deploy while a version is active; the run shows a warning instead.
- **Rollback by SHA.** `workflow_dispatch` with the SHA of any commit on `main` redeploys it with
  its existing images and its own deploy script.
- **Smoke test of the public URL** after every deploy: the page with its attribution, the API's
  health, and live data.

## Alternatives considered

- **An IAM user's access keys in GitHub secrets.** Simpler to set up, but a long-lived key in a
  third party's store, to rotate by hand, and broken by the rule of no keys. OIDC tokens last
  one job and can't be used from another repository or branch.
- **SSH from the runner** (a deploy key and port 22, or EC2 Instance Connect). Opens a port and
  adds a key to guard. SSM needs neither, and the agent is already there for Session Manager.
- **QEMU emulation on amd64 runners.** It works with any runner, but emulated Python
  dependency installs are several times slower. GitHub's arm64 runners are free for public
  repositories and build natively.
- **Building on the server.** It needs no registry. But a build next to Kafka and Postgres on
  2 GB is exactly the memory spike to avoid. A deploy would also no longer be the same image
  that was built and checked once.
- **Private GHCR packages or ECR.** Private images need registry credentials on the server: a
  GitHub token to rotate, or ECR with a pull permission and a repository per service. ECR also
  charges for storage past the free allowance. Nothing in the images is secret.
- **Blue-green or rolling deploys.** These need a second set of containers (no memory for it)
  or a load balancer (about €18 a month, against the rule of no load balancer). A minute of
  502s per merge is acceptable for a hobby service whose page shows an error state, and
  collection gaps of that size show up as gaps anyway.
- **Running `terraform apply` from Actions.** It would give CI admin-level rights over the
  account. Applies stay manual after a human reads the plan (CLAUDE.md); a `terraform plan` in
  CI is in the parking lot.

## Consequences

- No AWS keys and no SSH anywhere. The deploy role can't change infrastructure, read the data
  bucket or touch any other instance.
- Every merge to `main` is deployed and checked within about 10–15 minutes. A failed deploy
  shows as a red workflow with the server's output and logs.
- Each deploy costs a short gap in collection and in the API. A first deploy after a new
  instance imports the whole static GTFS and takes longer.
- Deploys depend on GitHub (Actions, GHCR) and on SSM. If GitHub is down, the running
  release is unaffected, but nothing can be deployed except by hand through Session Manager.
- One-time setup is manual: apply the Terraform, add five secrets, and make the packages public
  after their first push.
- Cost: €0 a month. IAM, SSM Run Command and GHCR for public packages are free. One
  invalidation path per deploy is far below CloudFront's 1,000 free paths a month.
- Revisit for zero-downtime deploys if the release outgrows one server or gets real users at
  rush hour, or if the repository or images ever become private.
