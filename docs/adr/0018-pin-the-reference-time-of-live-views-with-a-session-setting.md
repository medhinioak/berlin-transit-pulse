# ADR-0018: Pin the reference time of live views with a session setting

- Status: Accepted
- Date: 2026-11-19
- Decider: Medhini
- Review: options compared in the Claude Code session for #9, 2026-11-19

## Context

`marts.v_now_by_mode` (#9) counts trips whose predicted stop times span "now" and reads their
delay at the next stop after "now". In fixtures mode, ingest replays snapshots recorded on
2026-11-18 and rewrites only the header time (#6, #8 comment). Stop times stay as recorded.
Measured on 2026-11-19: at `now()`, 1 of the 172 fixture trips counts as running; at the
recording time (17:04:51 UTC), 130 do. With `now()` the endpoint is empty in fixtures mode,
so `make up` can't show real values and the integration test has nothing to check.

## Decision

The live views take "now" from `coalesce(nullif(current_setting('btp.as_of', true), '')::timestamptz, now())`.
metrics-api sets `btp.as_of` on every pooled connection when it has a reference time:
`BTP_AS_OF` if set, else, with `BTP_FEED_MODE=fixtures`, the header time of the last snapshot of
`BTP_FIXTURE_SET`. In live mode nothing is set and the views use `now()`. The response's
`as_of` shows the reference time, so fixture output is visibly from the recording.

## Alternatives considered

- **Shift all trip times in the ingest replay** by `now − recording time`: the data would
  look live everywhere (also for #10 and M3 finalisation). It changes the replay decided in
  #6, touches another service and needs its own PR and ADR first. It remains an option.
- **Keep `now()` and test only in live mode**: fixtures mode would show almost nothing, CI
  and `make up` couldn't check the read path, and tests would call the live feed.
- **A SQL function taking the time as a parameter**: changes the decided view into a
  function and makes every caller pass a time.

## Consequences

- Fixtures mode shows the 2026-11-18 rush hour with `as_of` 2026-11-18T17:04:51Z; `/health`
  still checks real freshness (`latest_feed_ts` is the rewritten header time).
- Any future "now" view uses the same expression, so it works in both modes.
- A wrong `BTP_AS_OF` in live mode would freeze the figures in time. Compose passes it
  through empty by default, the API logs it at start, and responses show it as `as_of`.
- Other sessions (psql, metrics-jobs) see `now()` unless they set `btp.as_of` themselves.
- Revisit if the ingest replay is changed to shift times: then the setting can go.
