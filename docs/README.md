# Documentation

The documentation is part of the repository so that a new developer, a reviewer or an auditor
can understand the whole project from here, without anything held elsewhere. If something you
need is missing, that is a bug: open an issue.

## Reading order

1. [`../README.md`](../README.md): what the project is and how to run it.
2. [`spec.md`](spec.md): the problem, users, version 1 scope, non-goals and definition of done.
3. [`architecture.md`](architecture.md): components, technology choices and trade-offs, what is
   built and what is planned.
4. [`plan.md`](plan.md): milestones, exit criteria and current status.
5. The README of the service you work on, under [`../services/`](../services/).

## By audience

| If you are… | Read |
| --- | --- |
| **A developer joining the project** | [process](process.md) (onboarding), [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`CLAUDE.md`](../CLAUDE.md) for the working rules, [glossary](glossary.md), [troubleshooting](troubleshooting.md), [compose stack](../deploy/compose/README.md), [fixtures](../fixtures/README.md) |
| **A reviewer or auditor** | [spec](spec.md), [architecture](architecture.md) (design rules, security and access, known limitations), [ADRs](adr/README.md), [risks](risks.md), [cost](cost.md), [AWS account](aws-account.md), [Terraform stacks](../deploy/terraform/README.md), [data sources and licensing](data-sources.md) |
| **Planning or managing delivery** | [plan](plan.md), [risks](risks.md), [cost](cost.md), [parking lot](parking-lot.md), [alternatives track](alternatives.md) |
| **Operating the system** | [architecture](architecture.md#observability-and-alerting), [AWS account](aws-account.md), [troubleshooting](troubleshooting.md); a runbook arrives with M2 |

## All documents

| Document | Content |
| --- | --- |
| [`spec.md`](spec.md) | Product specification |
| [`architecture.md`](architecture.md) | System architecture |
| [`plan.md`](plan.md) | Milestones, status, launch material |
| [`data-sources.md`](data-sources.md) | Input data, licences, rate limits, feed behaviour |
| [`cost.md`](cost.md) | Cost model, controls, shutdown plan |
| [`risks.md`](risks.md) | Risk register |
| [`alternatives.md`](alternatives.md) | Optional alternative implementations |
| [`adr/`](adr/README.md) | Architecture decision records |
| [`process.md`](process.md) | How work is done: roles, onboarding, change flow, review |
| [`aws-account.md`](aws-account.md) | AWS account baseline and conventions |
| [`troubleshooting.md`](troubleshooting.md) | Problems hit, causes and fixes |
| [`parking-lot.md`](parking-lot.md) | Ideas that are not in the plan |
| [`glossary.md`](glossary.md) | Terms |

Service and component documentation lives next to the code:
[ingest](../services/ingest/README.md),
[stop-event-processor](../services/stop-event-processor/README.md),
[schedule](../services/schedule/README.md),
[metrics-jobs](../services/metrics-jobs/README.md),
[metrics-api](../services/metrics-api/README.md),
[Kafka message contracts](../schemas/README.md),
[compose stack](../deploy/compose/README.md),
[Terraform stacks](../deploy/terraform/README.md),
[fixtures](../fixtures/README.md).

## Conventions

How these documents are kept true is in [`process.md`](process.md#keeping-the-documentation-true).
In short: docs change in the same pull request as the code, each fact has one home, measurements
carry a date, and status says whether something is built or planned.
