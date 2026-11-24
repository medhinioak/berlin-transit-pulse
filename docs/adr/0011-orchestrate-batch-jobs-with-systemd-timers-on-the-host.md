# ADR-0011: Orchestrate batch jobs with systemd timers on the host

- Status: Accepted
- Date: 2026-11-24
- Decider: Maintainer (@medhinioak)
- Review: decisions taken in issue #24 (first job: the schedule refresh), 2026-11-24

## Context

The release runs on one t4g.small with 2 GB RAM and no SSH (ADR-0020). Compose runs the
long-lived services, and deploys go through SSM Run Command (ADR-0012). Several batch jobs need a
clock:

- the static GTFS refresh and the activation of a staged timetable (#24);
- the metric jobs (#25, #26, #29);
- the Parquet export and the nightly `pg_dump` (#28).

Each runs for seconds to minutes, once or twice a day. A missed run must happen after a reboot,
and a run must be visible in the logs without logging in.

## Decision

Each job is a systemd service plus a timer on the host, versioned in `deploy/release/systemd/` as
`btp-<job>.service` and `btp-<job>.timer`. Each step of a deploy (`deploy.sh`):

- copies changed unit files to `/etc/systemd/system/`;
- removes the units an earlier deploy installed whose files are gone (it tracks them in
  `/opt/btp/installed_units`, so other `btp-*` units on the host are left alone);
- runs `daemon-reload` and `enable --now` on every timer.

The units are installed after the deploy has recorded `/opt/btp/deployed_sha`.

The service is `Type=oneshot` and calls `deploy/release/run-job.sh <name> <compose service>
<command…>`. That script:

- runs `docker compose -p btp-live … run --rm --no-deps --pull never` with the deployed
  release's image (`/opt/btp/deployed_sha`) and the rendered env file;
- refuses to run, with exit 1, while the checkout in `/opt/btp/repo` differs from that release.
  That is the case during a deploy, or after one failed before recording itself; an image and
  compose files of different releases are never mixed;
- logs start, end, duration and exit code.

Timers use `OnCalendar=… Europe/Berlin`, so they follow the service day across DST, with
`Persistent=true` so a run missed while the server was off happens at the next boot. Output goes
to the journal (`journalctl -u btp-<job>`). The current timers are in `docs/runbook.md`.

## Alternatives considered

- **Airflow or Dagster:** a scheduler, a web server and a metadata database would need more
  memory than the whole release stack has to spare, for a handful of daily jobs.
- **cron in a container, or a scheduler loop inside a service:** it runs only while that
  container is up, has no catch-up after a reboot, and its logs are mixed with the service's.
- **Host cron:** no `Persistent=` catch-up, no time zone per entry, no journal per job.
- **AWS (EventBridge Scheduler and SSM Run Command):** it works without the host clock, but each
  job then depends on IAM, SSM and a network round trip, and nothing runs locally.

## Consequences

- **Easier:**
  - a job is two small files, in the same PR as its code;
  - `systemctl list-timers 'btp-*'` shows the next and last run;
  - a run can be started by hand with `systemctl start btp-<job>`;
  - a job runs the image of the deployed release, so its code matches the services.
- **Harder:**
  - No UI and no retries beyond the next scheduled run. A failed run shows up only in the
    journal until monitoring reads it (M5).
  - Jobs run only on the release server. Locally the same commands are run by hand.
  - A one-shot container adds to the server's memory while it runs. Each job's peak has to fit
    in the memory budget (#20).
- **Cost:** none.
- **Revisit if:** jobs need dependencies between them, backfills or more than one host.
