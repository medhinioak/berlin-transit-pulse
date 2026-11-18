# Project

Live reliability metrics for Berlin public transport (on-time rate, delays, cancellations,
headway regularity per line, stop and hour), built from VBB's open GTFS-Realtime feed.
Python microservices, Kafka (single broker, KRaft), Postgres, Parquet on S3, Terraform on AWS.

Plan, scope and milestones: docs/plan.md. Decisions: docs/adr/. Process: docs/process.md.
Problems hit and their fixes: docs/troubleshooting.md. Ideas not in the plan: docs/parking-lot.md.
Tasks: GitHub Issues, one milestone per release (M1–M5, Alternatives, After launch).

# Commands

Keep this list true to the Makefile (`make help` lists every target).
Targets marked (stub) print the issue that implements them and exit non-zero.

- Install: make install (uv sync --all-packages)
- Start the stack locally: make up (Kafka, Postgres, all services; replays fixtures when
  BTP_FEED_MODE=fixtures) (stub, #3)
- Stop / follow logs: make down / make logs (stub, #3)
- Tests: make test (unit, with coverage; one test:
  uv run pytest services/ingest/tests/test_smoke.py::test_package_imports)
- Integration tests: make test-integration (needs make up; passes with no tests collected
  until #3 adds them)
- Lint and format: make lint / make fmt (ruff); type check: make typecheck (mypy strict, per package)
- Pre-commit hooks: uv run pre-commit run --all-files
- Record new feed fixtures: make fixtures (polite to the feed; see Rules) (stub, #4)
- Terraform: make tf-plan STACK=release (fmt, validate, plan). Never apply from here. (stub, #13)
- GitHub: use the gh CLI for issues, PRs and CI
  (gh issue view N, gh pr checks, gh run view --log-failed)

# Rules

## Service boundaries

- One folder per service under services/. A service never imports another service's code;
  shared code lives in libs/ and stays small (GTFS parsing, Kafka/DB helpers).
- Services talk only through Kafka topics or the metrics API. Message schemas live in
  schemas/; changing one needs a note in the PR and, if consumers break, an ADR.
- Each service writes only to its own Postgres schema with its own role
  (ingest: none, processor: events, schedule: schedule, metrics-jobs: marts).
  metrics-api uses a read-only role.
- Code under alt/, deploy/terraform/aws-microservices/ and deploy/terraform/eks/ is never
  imported or required by the release path. Each alternative runs with one command and is
  exercised in CI on the shared fixtures, or it is removed.

## Data correctness

- Writes are idempotent. Stop events are keyed by (trip_id, service_date, stop_sequence);
  replaying a topic or re-running a job must give the same tables.
- Store timestamps in UTC. Use Europe/Berlin only for GTFS service-day logic and display.
  GTFS times can exceed 24:00; never parse them as clock times.
- Metric definitions (on-time windows per operator, percentiles, coverage thresholds,
  headway regularity) are fixed by the metric-definitions ADR (planned ADR-0005, M3).
  Until it exists, ask before choosing a definition. Changing one later needs a new ADR.
- Never invent or hard-code numbers shown to users. Every figure on the site comes from the
  pipeline, and hours below the coverage threshold are shown as gaps, not as values.

## Feed etiquette (VBB GTFS-RT, CC-BY 4.0)

- Never exceed 7 requests per minute to production.gtfsrt.vbb.de. Always send gzip,
  If-None-Match and a User-Agent with contact details.
- Tests and CI never call the live feed. They use recorded snapshots in fixtures/.
- Every public page and the README carry the CC-BY 4.0 attribution to VBB.

## Code quality

- **Simplicity:** readable code, no abstractions for hypothetical future needs. This is a
  solo, part-time project; prefer the boring option.
- **Types and docstrings:** type hints on every function; a one-line docstring on every
  public function, class and module. mypy and ruff pass without blanket ignores.
- **Docs first:** a feature or architectural change has an issue with acceptance criteria,
  and an ADR when it is a decision, before implementation starts.

## Testing

- Aim for about 80% coverage on pure logic: snapshot diffing, stop-event finalisation,
  GTFS matching, metric calculations.
- Test the edge cases that matter here: duplicate and repeated snapshots, late updates,
  vanished trips, cancellations, trips past midnight, DST changes, timetable version swaps.
- No tests that only pad coverage (getters, framework wiring).

## Infrastructure, cost and secrets

- All AWS resources come from Terraform in deploy/terraform/. No console changes except
  the account baseline (MFA, Identity Center, budgets).
- Claude Code never runs terraform apply or terraform destroy. Medhini applies after
  reading the plan; the plan output goes in the PR.
- Tag every resource Project=berlin-transit-pulse and Stack=release|poc.
- Budget: release under €20/month. A change that adds monthly cost states the estimated
  € delta in the PR. Release stack: no NAT gateway, no load balancer, no managed Kafka.
- No secrets in the repo. AWS access is SSO locally and OIDC in GitHub Actions.
  Never read .env files, *.tfvars or ~/.aws.

## Web UI

- Mobile first; WCAG 2.2 AA for interactive parts (keyboard, focus, labels, contrast).

## Workflow and Git

- Conventional Commits with the service as scope: feat(ingest): …, fix(processor): …,
  docs(adr): …, ci: …, chore(infra): …. One topic per pull request.
- Work on a branch, never on main. PR bodies start with "Closes #N" and fill in the
  template, including the AI involvement block. Keep the Co-Authored-By trailer.
- A decision made in the PR gets its ADR in the same PR.

# Gotchas

- The VBB feed is a FULL_DATASET of TripUpdates only: no vehicle positions, no alerts.
  ~10 MB uncompressed, ~2.3 MB gzipped, ~10k trips per snapshot. It covers all of VBB
  including Brandenburg; the Berlin filter is defined in the schedule service.
- The feed contains trips that haven't started or have finished. Zero delay on those is
  not a measurement.
- The VBB static GTFS uses extended route types: 109 S-Bahn, 400s U-Bahn, 700s bus,
  900s tram, 100s regional rail, 1000s ferry.
- VBB announces outages and rate-limit changes in https://production.gtfsrt.vbb.de/news.atom.xml.
  VBB reports missing data since 2026-06-04; all modes, S-Bahn included, had live delays
  at rush hour on 2026-11-18 (docs/troubleshooting.md).
- The feed lists most of the day's S-Bahn trips in advance. Shares computed over all trips in
  a snapshot are meaningless; count only trips that are running or have run.
- Annual timetable change on 2026-12-13: the static GTFS version changes and trip IDs
  with it.
- The release server is a t4g.small (arm64, 2 GB RAM): build arm64 images, keep Kafka's
  heap at about 384–512 MB, and check memory after adding anything to compose.
