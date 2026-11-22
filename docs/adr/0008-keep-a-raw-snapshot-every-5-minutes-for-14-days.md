# ADR-0008: Keep a raw snapshot every 5 minutes for 14 days, not every poll

- Status: Accepted
- Date: 2026-11-22
- Decider: Maintainer (@medhinioak)
- Review: decisions fixed in issue #19 before implementation; the logging change was agreed with the maintainer while planning; implementation reviewed in the pull request that closes #19, 2026-11-22

## Context

Kafka keeps `trip-updates` for 24 h and Postgres keeps only the latest prediction per stop.
After a bug, or when the stop-event definition (ADR-0004) or the metric definitions (ADR-0005)
change in M3, the only way to re-run the pipeline over past days is the raw feed. Recorded
snapshots are also how new fixtures and the blog's numbers can come from real data.

The VBB feed is a FULL_DATASET of about 10 MB, **about 2.3 MB gzipped** (CLAUDE.md,
[data sources](../data-sources.md#data-volume-and-what-it-means-for-storage)), polled every
30 s. Keeping every poll would be about 6.6 GB per day, roughly 200 GB a month. The release
budget is €20 a month ([cost](../cost.md)).

## Decision

In live mode the ingest service uploads one snapshot it has already fetched to
`s3://btp-data-<account>-eu-north-1/raw/vbb-rt/date=YYYY-MM-DD/hour=HH/YYYYMMDDTHHMMSSZ.pb.gz`
for the first snapshot of each 5-minute slot of **feed header time**. Slots are aligned to the
epoch (:00, :05, … UTC), so a day has 288 objects whatever the poll timing. The S3 lifecycle
rule from #16 deletes objects after 14 days.

- **What is stored:** the response body exactly as received (gzip), with `Content-Type:
  application/x-protobuf`, `Content-Encoding: gzip` and metadata `feed-ts`, `etag` and
  `entity-count`. A body sent without gzip is compressed at level 6, in the upload thread. No
  extra request goes to VBB. Clients that honour `Content-Encoding` may unpack the object on
  download; replays read it with boto3 or the AWS CLI, which keep the gzip bytes.
- **Slots, not "5 minutes since the last upload":** issue #19 planned the latter. With 30 s
  polls and jittery header times it drifts, giving gaps of 300–330 s and often 11 objects an
  hour. A failed upload leaves its slot open, so the next snapshot in the slot retries.
- **Keys** are UTC from the header timestamp, in Hive-style partitions, so DuckDB or Athena
  can read them later. A snapshot uploaded twice (a restart) gets the same key and overwrites
  itself.
- **Never in the way of polling:** one background thread with a queue of one. A due snapshot
  that arrives while an upload is still running is skipped and counted (`raw_skipped_busy`).
  A failure is logged and counted (`raw_failed`) without retries beyond boto3's standard
  retry mode. It doesn't move the last-success time, so the next poll tries again. On SIGTERM
  the service waits at most 10 s for a running upload; ingest's stop grace period is 60 s so
  that fits after the Kafka flushes.
- **Logging:** the upload is asynchronous, so the `poll` line can't say whether it
  succeeded. It carries `raw` = `queued`, `skipped_busy` or `not_due`. The worker writes its
  own `raw upload` line with `raw_key`, `raw_bytes`, `duration_ms`, `ok`, the totals and, on
  failure, `error`. This replaces issue #19's plan of putting `raw_uploaded`, `raw_key` and
  `raw_bytes` on the poll line.
- **Off by default:** only with `BTP_FEED_MODE=live` and `BTP_RAW_BUCKET` set. Fixtures mode,
  local runs, tests and CI never touch S3. The release sets the bucket from the SSM parameter
  `/btp/release/data_bucket`.

## Alternatives considered

- **Every poll (30 s)** — about 6.6 GB a day and, with 14 days kept, about 90 GB stored plus
  ten times the PUT requests. That is roughly ten times the S3 cost for no gain a replay
  needs, since the published changes already carry every poll for 24 h.
- **Diffs only (rebuild from Kafka messages)** — the changed-trip stream is already derived
  data; a bug in the diff would be baked into the archive.
- **Upload in the poll loop** — a slow S3 call would delay the next poll and could stall
  ingest; a background thread keeps polling on time.
- **Longer retention** — Parquet history (ADR-0007) keeps the derived events long term; 14 days
  is enough to notice a bug and re-run.

## Consequences

- Any 5-minute instant of the last 14 days can be replayed through the processor.
- About 288 objects a day, **~660 MB a day and ~9 GB stored** at 2.3 MB each (an estimate from
  the documented feed size; the fixture files are trimmed, so they can't be used to measure).
  At S3 Standard prices that is roughly €0.25 a month for storage and €0.05 for about 8,600
  PUT requests (`~` estimates, not re-priced for eu-north-1; [cost](../cost.md)). The measured per-object and per-day size from the server
  replaces these estimates after the first deploy (follow-up to #19).
- Up to 5 minutes between snapshots: replays see the feed at that resolution, not every 30 s.
- A failed or skipped upload only leaves a gap in the archive; the live pipeline is unaffected.
- Revisit if the feed size changes a lot, if replays need finer resolution, or if the bucket
  cost shows up in the monthly review.
