# Contributing

Thanks for helping. This page is the short version; the full process is in
[`docs/process.md`](docs/process.md) and the working rules are in [`CLAUDE.md`](CLAUDE.md).

## Get set up

```sh
make install                       # needs uv and Docker; see the README
make up                            # the whole stack, offline (fixtures)
make test
```

## The short flow

1. Work from a GitHub issue with acceptance criteria. Ask the maintainer if there is none.
2. Branch from `main` (`feat/…`, `fix/…`, `docs/…`, `ci/…`, `chore/…`, `test/…`).
3. Make `make lint`, `make typecheck` and `make test` pass.
4. Open a pull request from the template. The title is a Conventional Commit with the service
   as scope, for example `feat(ingest): publish changed trips to Kafka`. One topic per pull
   request.
5. If you made a decision, add an ADR in the same pull request. If you changed behaviour, update
   the affected documentation in the same pull request.

## Ground rules

- Tests and CI never call the live VBB feed. Use the recorded fixtures.
- A service never imports another service's code, and writes only to its own Postgres schema.
- No secrets in the repository.
- Infrastructure is Terraform only. Include the `terraform plan` output and the estimated
  monthly cost change in the pull request; the maintainer applies it.
- Say in the pull request template whether an AI assistant wrote or reviewed the change.

Contributions are made under the project's [MIT licence](LICENSE).
