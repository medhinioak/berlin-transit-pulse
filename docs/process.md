# How work is done here

A solo, part-time project run like a small team's, so the history shows what was built,
how and why. Work happens in bursts, so everything needed to pick up where you left off
lives in the repository: issues, ADRs, this file and the troubleshooting log.

## Where things live

| What | Where |
| --- | --- |
| Goals, scope, milestones and their exit criteria | [`plan.md`](plan.md) |
| Decisions and their reasons | [`adr/`](adr/), one file per decision |
| Tasks | GitHub Issues, one milestone per release; the Projects board shows status |
| Ideas not in the plan | [`parking-lot.md`](parking-lot.md) |
| Problems hit, their causes and fixes (feed, infra, tooling) | [`troubleshooting.md`](troubleshooting.md) |
| How to deploy, roll back, restore and shut down | `runbook.md`, created with the M2 release stack |
| Instructions for Claude Code | [`../CLAUDE.md`](../CLAUDE.md) |
| Releases | Git tags and GitHub Releases: v0.1 (M1) … v1.0 (M5) |

## The flow for every change

1. **Issue.** What and why, plus acceptance criteria as a checklist, in a milestone.
   Most already exist; new work gets an issue first.
2. **Branch** from `main`: `feat/…`, `fix/…`, `docs/…`, `ci/…`, `chore/…`, `test/…`,
   named after the issue (`feat/12-diff-snapshots`).
3. **Plan, then build.** For anything non-trivial, plan first (Claude Code plan mode).
   Build against checks that pass or fail: tests on recorded fixtures, mypy, ruff,
   `terraform validate`.
4. **Pull request** using the template. The title is a Conventional Commit with the service
   as scope (`feat(ingest): publish changed trips to Kafka`), because it becomes the squash
   commit message. Link the issue with `Closes #12`.
5. **Checks.** CI must be green. Infrastructure PRs include the `terraform plan` output
   and the estimated monthly cost change.
6. **Review.** Run `/code-review` in Claude Code before opening the PR, then read the full
   diff yourself. Get an independent review (a different model, or a fresh session that
   didn't write the code) for the risky kinds of change listed below. Fix or answer every
   comment.
7. **Squash-merge.** The branch is deleted automatically and the issue closes.
8. **Decision made?** Add an ADR in the same pull request.
9. **Apply.** For Terraform changes, Medhini runs `terraform apply` after the merge and
   notes the result in the PR. Claude Code never applies.

### Changes that need an independent review

| Change | Why |
| --- | --- |
| Terraform, IAM, GitHub Actions workflows | Security and cost mistakes are expensive and quiet |
| Stop-event finalisation, deduplication, idempotency | Errors silently corrupt every metric |
| Metric definitions and coverage thresholds | They decide what commuters are told |
| Kafka topic, key or schema changes | They can break consumers and replays |

## Milestone gates

At the end of each milestone:

1. Tick the exit criteria in `plan.md`, or record why an item was cut.
2. Review `parking-lot.md`: promote, keep or drop each idea.
3. Check the AWS bill against the budget.
4. Tag the release and publish a GitHub Release with a short summary of what shipped.

## Writing an ADR

Copy [`adr/0000-template.md`](adr/0000-template.md), number it next in sequence, add it
to the index in [`adr/README.md`](adr/README.md) and keep it to one page. Accepted ADRs
aren't edited; a changed decision gets a new ADR that marks the old one
"Superseded by ADR-NNNN".

Decisions discussed in a chat (for example the planning chat in the Claude project) are
not final until they are an ADR merged through a PR. Before accepting, ask a second model
for the strongest case against the chosen option and record it in the ADR's "Review" line.

## Recording problems

When something breaks and takes more than a few minutes to understand (feed outages,
schema surprises, memory pressure, Terraform errors), add an entry to
[`troubleshooting.md`](troubleshooting.md) in the PR that fixes or works around it.

## Working with Claude Code

- `CLAUDE.md` holds only what can't be read from the code: commands, rules, gotchas.
  Plans and decisions live in `docs/`; `CLAUDE.md` points to them.
- One issue per session. Run `/clear` when switching topics or after two failed corrections.
- Rules that must never break are enforced by tooling (ruff, mypy, tests, CI, the
  permissions in `.claude/settings.json`), not only by instructions.
- Personal preferences go in `CLAUDE.local.md`, which isn't committed.

## AI involvement

Claude Code is the author of most changes; Medhini decides and merges.
- Pull requests: the template's "AI involvement" block lists the author and reviewers.
- Commits: keep the `Co-Authored-By` trailer that Claude Code adds.
- ADRs: the "Review" line names who reviewed the decision.
- Facts about results (metrics, findings, uptime, costs) in the README, site or blog
  come from the pipeline or from Medhini, never from an AI's guess.
