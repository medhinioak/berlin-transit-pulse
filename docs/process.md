# How work is done here

The project is run like a small team's, although it currently has one maintainer. The aim is
that its history shows what was built, how and why, and that the work can be handed over to
someone new. Work happens in bursts, so everything needed to pick up where someone left off
lives in the repository: issues, ADRs, these docs and the troubleshooting log.

## Roles

| Role | Who | Responsibilities |
| --- | --- | --- |
| **Maintainer** | @medhinioak | Sets scope and priorities, decides, merges, owns the AWS account, applies Terraform changes |
| **Contributor** | Anyone sending a pull request, including an external developer | Works on issues through pull requests. Does not apply infrastructure changes |
| **Independent reviewer** | A person, a different model, or a fresh session that did not write the code | Reviews the risky kinds of change listed [below](#changes-that-need-an-independent-review) |

## Where things live

| What | Where |
| --- | --- |
| Documentation index and reading order | [`README.md`](README.md) |
| What the product is, scope, definition of done | [`spec.md`](spec.md) |
| How the system is built and why | [`architecture.md`](architecture.md) |
| Milestones, exit criteria and status | [`plan.md`](plan.md) |
| Input data, licences, feed behaviour | [`data-sources.md`](data-sources.md) |
| Cost model and shutdown plan | [`cost.md`](cost.md) |
| Risks and mitigations | [`risks.md`](risks.md) |
| Optional alternative implementations | [`alternatives.md`](alternatives.md) |
| Decisions and their reasons | [`adr/`](adr/), one file per decision |
| Tasks | GitHub Issues, one milestone per release; the Projects board shows status |
| Ideas not in the plan | [`parking-lot.md`](parking-lot.md) |
| Problems hit, their causes and fixes (feed, infra, tooling) | [`troubleshooting.md`](troubleshooting.md) |
| How to deploy, roll back, restore and shut down | [`runbook.md`](runbook.md) |
| AWS account setup, region and sign-in | [`aws-account.md`](aws-account.md) |
| Terms | [`glossary.md`](glossary.md) |
| Working rules and gotchas, written for AI coding assistants and binding on everyone | [`../CLAUDE.md`](../CLAUDE.md) |
| Releases | Git tags and GitHub Releases: v0.1 (M1) … v1.0 (M5) |

## Onboarding a new developer

1. Read [`README.md`](../README.md), then the [documentation index](README.md) in its order:
   spec, architecture, plan. Then read the README of the service you will touch.
2. Run it locally with recorded data and no network access: `make install`, then
   `make up` (fixtures mode by default), then `make test`. Details are in the root README.
3. Pick an issue in the current milestone. Every issue should have acceptance criteria; if one
   does not, ask the maintainer before starting.
4. You do not need AWS access for service work. Infrastructure changes come as Terraform in a
   pull request with the `terraform plan` output; the maintainer applies them. Access for
   additional people is not defined yet ([`aws-account.md`](aws-account.md#access-for-additional-people)).
5. Never call the live VBB feed from tests. Read the [rules for using the feed](data-sources.md#rules-for-using-the-feed)
   before running anything in live mode.

## The flow for every change

1. **Issue.** What and why, plus acceptance criteria as a checklist, in a milestone. Most
   already exist; new work gets an issue first.
2. **Branch** from `main`: `feat/…`, `fix/…`, `docs/…`, `ci/…`, `chore/…`, `test/…`, named
   after the issue (`feat/12-diff-snapshots`).
3. **Plan, then build.** For anything non-trivial, plan first. Build against checks that pass
   or fail: tests on recorded fixtures, mypy, ruff, `terraform validate`.
4. **Pull request** using the template. The title is a Conventional Commit with the service as
   scope (`feat(ingest): publish changed trips to Kafka`), because it becomes the squash
   commit message. Link the issue with `Closes #12`.
5. **Checks.** CI must be green. Infrastructure pull requests include the `terraform plan`
   output and the estimated monthly cost change.
6. **Review.** Run an AI-assisted review first (for example `/code-review` in Claude Code), then
   read the full diff yourself. Get an independent review for the risky kinds of change
   listed below. Fix or answer every comment.
7. **Squash-merge.** The branch is deleted automatically and the issue closes.
8. **Decision made?** Add an ADR in the same pull request.
9. **Apply.** For Terraform changes, the maintainer runs `terraform apply` after the merge and
   notes the result in the pull request. AI coding agents never apply.

### Changes that need an independent review

| Change | Why |
| --- | --- |
| Terraform, IAM, GitHub Actions workflows | Security and cost mistakes are expensive and quiet |
| Stop-event finalisation, deduplication, idempotency | Errors silently corrupt every metric |
| Metric definitions and coverage thresholds | They decide what commuters are told |
| Kafka topic, key or schema changes | They can break consumers and replays |

## Milestone gates

At the end of each milestone:

1. Update the status snapshot and tick the exit criteria in [`plan.md`](plan.md), or record why
   an item was cut.
2. Review [`parking-lot.md`](parking-lot.md): promote, keep or drop each idea.
3. Review [`risks.md`](risks.md).
4. Check the AWS bill against the budget.
5. Tag the release and publish a GitHub Release with a short summary of what shipped.

## Writing an ADR

Copy [`adr/0000-template.md`](adr/0000-template.md), number it next in sequence, add it to the
index in [`adr/README.md`](adr/README.md) and keep it to one page. Accepted ADRs are not
edited, apart from corrections to their metadata and links, which the pull request says. A
changed decision gets a new ADR that marks the old one "Superseded by ADR-NNNN".

A decision discussed outside the repository (a chat, a call, a meeting) is not final until it
is an ADR merged through a pull request. Before accepting, ask a second reviewer or model for
the strongest case against the chosen option and record it in the ADR's "Review" line.

## Recording problems

When something breaks and takes more than a few minutes to understand (feed outages, schema
surprises, memory pressure, Terraform errors), add an entry to
[`troubleshooting.md`](troubleshooting.md) in the pull request that fixes or works around it.

## Keeping the documentation true

- **Docs change with the code.** A pull request that changes behaviour, a rule or a decision
  updates the affected docs in the same pull request.
- **One topic, one home.** Each fact lives in one document and others link to it. The table
  above says which.
- **Self-contained.** Docs do not point to material outside the repository (a chat, a private
  project, a personal note). A fact that matters is written down here.
- **Dated measurements.** A measured number states when and how it was measured.
- **No personal or account details.** No account IDs, sign-in URLs, email addresses, credentials
  or personal notes.
- **Status is explicit.** Say whether something is built or planned, with its issue.

## Working with AI coding assistants

- `CLAUDE.md` holds only what cannot be read from the code: commands, rules, gotchas. Plans and
  decisions live in `docs/`; `CLAUDE.md` points to them.
- One issue per session. Clear the context when switching topics or after two failed
  corrections.
- Rules that must never break are enforced by tooling (ruff, mypy, tests, CI, the permissions in
  `.claude/settings.json`), not only by instructions.
- Personal preferences go in `CLAUDE.local.md`, which is not committed.

## AI involvement

An AI coding assistant (Claude Code) authors most changes; the maintainer decides and merges.

- Pull requests: the template's "AI involvement" block lists the author and the reviews.
- Commits: keep the `Co-Authored-By` trailer the assistant adds.
- ADRs: the "Review" line names who reviewed the decision.
- Facts about results (metrics, findings, uptime, costs) in the README, on the site or in
  write-ups come from the pipeline or from a person, never from an AI's guess.
