# Alternatives track (optional)

Optional implementations of parts of the pipeline on other widely used technologies. They show
how the same job maps onto a different stack, proven on the same recorded fixtures. **None of
them is on the release path:** the release does not import, build or deploy any of this code.
Tracked by the GitHub milestone "Alternatives" (#46–#57).

## Rules

These keep the track from turning into dead code:

1. An alternative implements the same job as the release choice and is checked by running it on
   the shared recorded feed in `fixtures/`, not by being deployed. The exception is the
   one-off proof of concept, which is applied, measured and destroyed.
2. It runs with one command, locally or in CI, and has a short README comparing it with the
   release choice. Without that it is removed.
3. Code under `alt/`, `deploy/terraform/aws-microservices/`, `deploy/terraform/eks/` and
   `helm/` is never imported or required by the release.
4. Work starts after M3, once there is real code to port, and follows the priority order below.
   It never delays a release.

## The alternatives, in priority order

Suggested start: rows 1–5.

| # | Layer | Release version | Alternative in the repo | How it is proven | Issue |
| ---: | --- | --- | --- | --- | --- |
| 1 | Container orchestration | docker compose on EC2 | Kubernetes: Helm chart with probes, resource limits, an HPA, ConfigMaps and Secrets; EKS Terraform module | Chart installed on a `kind` cluster in GitHub Actions, replaying recorded feeds; `terraform plan` for EKS | #46, #47 |
| 2 | Cloud topology | One EC2 server | Microservices on ECS Fargate, MSK Serverless and RDS | Deployed once, then destroyed; screenshots, metrics and cost in the README | #48 |
| 3 | Stream processing | Python consumer | Apache Flink (PyFlink) or Spark Structured Streaming doing the same stop-event finalisation | Both run on the same recorded feed and a test compares their outputs | #49 |
| 4 | Lakehouse | Parquet and DuckDB | Apache Iceberg tables (PyIceberg with a local catalog; AWS Glue catalog in Terraform, plan only); the same dbt models through dbt-duckdb | dbt runs green on both Postgres and DuckDB/Iceberg | #50 |
| 5 | Observability | CloudWatch metrics and alarms | OpenTelemetry instrumentation, Prometheus and Grafana dashboards as code, as a compose profile | `make observe` brings it up locally with live dashboards | #51 |
| 6 | Batch orchestration | systemd timers | Airflow DAGs or Dagster assets for dbt, export and refresh (pick one) | Runs locally in compose | #52 |
| 7 | Streaming platform | Kafka | Redpanda as a drop-in, plus a schema registry with Protobuf schemas for `trip-updates` | The same integration tests pass on both brokers | #53 |
| 8 | GitOps | Deploys through SSM | Argo CD managing the Helm chart | Runs inside the `kind` cluster | #54 |
| 9 | Infrastructure quality | Terraform | `tflint`, `checkov` (policy as code), Terratest for one module | Runs in CI on every pull request | #55 |
| 10 | Data quality | Custom checks and dbt tests | Soda Core or Great Expectations with the same rules | Runs in CI | #56 |
| 11 | AI layer (optional) | None | Plain-language summaries of a line's day, or questions over the metrics turned into SQL, through Bedrock, with a small evaluation set | Notebook or local service; not part of the public site | #57 |

## Layout

```text
alt/                          flink/, iceberg/, otel/, dagster/ ...
helm/transit/                 Helm chart, tested on kind in CI
deploy/terraform/
  aws-microservices/          proof of concept: apply, demo, destroy
  eks/                        plan only
```

## Cost and open ideas

- EKS for a single afternoon would cost a few dollars (the control plane is roughly
  $0.10 per hour), so it could replace or join the ECS proof of concept later. It is in the
  [parking lot](parking-lot.md).
- The proof of concept costs about $4–6 per run ([cost](cost.md#microservices-proof-of-concept-one-off)).
- Several ADRs (planned ADR-0014 to ADR-0016) are written once the proof of concept exists.
