# Message schemas

Contracts for the Kafka topics. Changing one needs a note in the PR and, if consumers break,
an ADR (CLAUDE.md, Service boundaries).

| Topic | Producer | Consumers | Contract |
| --- | --- | --- | --- |
| `trip-updates` | ingest | processor | [trip-updates.md](trip-updates.md) |
