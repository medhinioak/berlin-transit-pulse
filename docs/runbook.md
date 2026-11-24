# Runbook

How to operate the release: deploy, roll back, look inside the server, and what to do on the
days that need a hand. The server is one t4g.small in `eu-north-1` running the whole stack with
docker compose ([ADR-0020](adr/0020-run-the-release-on-one-t4g-small-with-no-ssh.md)), deployed
by GitHub Actions through SSM
([ADR-0012](adr/0012-deploy-with-terraform-github-actions-and-ssm-without-ssh.md)). There is no
SSH. Infrastructure: [`deploy/terraform/release/README.md`](../deploy/terraform/release/README.md).

Everything that needs AWS credentials or repository admin is done by the maintainer. Commands
on your machine assume `aws login --profile btp` and the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).

## How a deploy happens

Every push to `main` whose CI run passes starts
[`.github/workflows/release.yml`](../.github/workflows/release.yml):

1. **meta** picks the commit: the one CI tested. If main has moved on since, it skips (the newer
   commit's run deploys).
2. **images** builds `ghcr.io/medhinioak/btp-<service>:<sha>` for every service with a
   Dockerfile, natively on an arm64 runner, and moves the `:main` tag. If an image for the SHA
   already exists, it is reused.
3. **deploy** signs in to AWS through OIDC (role `btp-release-deploy`) and sends one SSM Run
   Command to the instance. On the instance:
   - it checks out the SHA in `/opt/btp/repo`;
   - it runs [`deploy/release/deploy.sh`](../deploy/release/deploy.sh), which renders
     `/opt/btp/.env` from SSM Parameter Store `/btp/release/*`;
   - it pulls the images and restarts the stack as compose project `btp-live` with
     `compose.yaml` and `compose.release.yaml`;
   - it waits for the one-shots to exit 0 and the services to be healthy;
   - it writes `/opt/btp/deployed_sha` and removes all but this and the previous release's
     images.

   The output appears in the job log.
4. **web** uploads `web/` to the web bucket (assets first, `index.html` last) and invalidates
   CloudFront (`/*`).
5. **smoke** checks `https://<cloudfront-domain>/`:
   - the page has the VBB attribution;
   - `/api/health` returns 200 within 5 minutes;
   - `/api/v1/now/by-mode` has a mode with running trips.

   On failure it prints `ps`, the last log lines and `free -m` from the server.

Containers restart during step 3. Expect a minute or two of 502s on `/api/*`; the page shows its
error state meanwhile. Collection pauses for the same time.

## Redeploy or roll back

Run the workflow by hand with the SHA of any commit on `main`. Images for old SHAs stay in GHCR,
so nothing is rebuilt:

```bash
gh workflow run release.yml -f sha=<40-character sha>   # roll back (or forward) to that commit
gh workflow run release.yml                             # redeploy main's tip
gh run watch                                            # follow it
```

The SHA must be on `main`, have passed CI, and come from #18 onwards (older commits have no
`deploy/release/deploy.sh`); anything else is refused. A rollback runs the old commit's
`deploy.sh` and compose files too, so it really is the old release, Postgres data excepted:
schema changes made by a newer release are not undone. The next push to `main` deploys `main`
again. To stay on the old version, revert on `main` instead.

Runs queue one at a time and are never cancelled midway. GitHub keeps only one *waiting* run,
though: if a rollback is waiting and a push to `main` queues a deploy, the newer one replaces it.
Check `gh run list -w release.yml` after starting a rollback while another run is in progress.

## Open a shell on the server

```bash
aws ssm start-session --profile btp \
  --target "$(terraform -chdir=deploy/terraform/release output -raw instance_id)"
```

In the session, become root and define the compose command once:

```bash
sudo -i
cd /opt/btp/repo/deploy/compose
export BTP_IMAGE_TAG=$(cat /opt/btp/deployed_sha)
dc() { docker compose -p btp-live --env-file /opt/btp/.env -f compose.yaml -f compose.release.yaml "$@"; }
dc ps -a                # containers, health, exit codes of the one-shots
```

`/opt/btp/.env` holds the Postgres passwords (mode 600). Don't copy it off the server.

## See logs

```bash
dc logs --tail=100 ingest              # one service
dc logs --tail=50 -f                   # everything, follow
dc logs --since=1h stop-event-processor
free -m && docker stats --no-stream    # memory per container
```

Docker keeps 3 × 10 MB of logs per container (cloud-init sets the rotation), so logs older than
that are gone.

## Restart one service

```bash
dc restart ingest                           # same container, same image
dc up -d --no-build --no-deps ingest        # recreate it (after changing /opt/btp/.env, say)
```

Don't run `dc build` or `dc up` without `--no-build`: the server never builds images. A change
to the code goes through a pull request and the deploy.

## Scheduled jobs (timers)

Batch jobs are systemd timers on the host
([ADR-0011](adr/0011-orchestrate-batch-jobs-with-systemd-timers-on-the-host.md)). The unit files
are in `deploy/release/systemd/`, and each deploy installs or updates them. Each job runs a
one-shot container of the deployed release through `deploy/release/run-job.sh`.

| Timer | When (Europe/Berlin) | Runs |
| --- | --- | --- |
| `btp-schedule-refresh` | daily 03:30, plus up to 15 min | `btp-schedule refresh`: download if changed, import, stage or activate, back up the zip, prune |
| `btp-schedule-activate` | daily 23:40 | `btp-schedule activate-due`: activate a staged version starting tomorrow |

```bash
systemctl list-timers 'btp-*'                         # next and last run of each
journalctl -u btp-schedule-refresh --since today      # output and "run-job: … exit N"
systemctl start btp-schedule-refresh                  # run now (waits until it finishes)
systemctl status btp-schedule-refresh                 # result of the last run
```

Missed runs (the server was off) run at the next boot. A failed run is retried at the next
scheduled time. Until monitoring exists (M5), check the journal.

## Database

```bash
dc exec postgres psql -U postgres -d btp
```

The Postgres roles get their passwords from the env file **on the first start only**
(`postgres/init/`). A new value in SSM reaches `/opt/btp/.env` on the next deploy, but the role
keeps the old password and the service then fails to log in. To rotate one, set the new password
with `ALTER ROLE … PASSWORD …` in psql, then redeploy.

## 13 December 2026: the timetable change

VBB's annual timetable starts on Sunday 2026-12-13, with new trip IDs. The timers handle it
([schedule README](../services/schedule/README.md#staging-activation-and-pruning)):

- once VBB publishes the zip (at most 21 days ahead), the 03:30 refresh imports it as `ready`
  with `activate_on` 2026-12-13;
- at 23:40 on the 12th, `activate-due` activates it;
- trips of the 12th still running after midnight keep their old version.

**VBB may not publish a separate zip.** The zip of 2026-11-20 (`8255349c4cd3`, checked
2026-11-24) already runs to 2027-12-11. It holds 70,811 trips that run only from 13 December,
with their new trip IDs. Any version imported from it or a later zip already covers the 13th,
with no swap at all. Check that the active version has trips on the 13th:

```bash
dc exec postgres psql -U postgres -d btp -c "
  select count(distinct t.trip_id) from schedule.v_trips t
  join schedule.v_calendar_dates d using (service_id)
  where d.date = '2026-12-13' and d.exception_type = 1"
```

Tens of thousands of trips: nothing to do on the 13th but the API check below. Zero (the active
version is older): the next 03:30 refresh imports the current zip. Or run the refresh by hand
(manual fallback below).

**Verify** between 10 and 12 December, and again on the morning of the 13th:

```bash
dc run --rm --no-deps -T schedule-import btp-schedule status
journalctl -u btp-schedule-refresh --since -2d
journalctl -u btp-schedule-activate --since -2d   # on the 13th: "version … is now active"
```

If VBB publishes a zip starting on the 13th, it should be `ready` with
`activate_on=2026-12-13` before then. On the 13th it should be `active`, and the previous one
`retired`. Either way, check from your machine on the 13th that live data still matches:

```bash
curl -s "https://$(terraform -chdir=deploy/terraform/release output -raw cloudfront_domain_name)/api/v1/now/by-mode" | jq
```

Every mode should still show `running_trips > 0`. If it doesn't, roll back to the previous
version (`btp-schedule activate <id>`, as below) and investigate.

**Manual fallback**, if nothing is staged by the 12th (VBB late, refreshes failing) or the
activation didn't run. It can be used at any time on the 12th or 13th:

```bash
# Max age 0: ignore the cached zip, which may be the old timetable.
dc run --rm --no-deps -T -e BTP_SCHEDULE_MAX_AGE_HOURS=0 schedule-import btp-schedule refresh
dc run --rm --no-deps -T schedule-import btp-schedule activate-due   # on the 12th
dc run --rm --no-deps -T schedule-import btp-schedule status
```

On the 12th the refresh stages the new zip, and `activate-due` activates it for the 13th. On
the 13th the refresh activates it immediately, and it still counts from 00:00 on the 13th
(ADR-0023). Roll back with `dc run --rm --no-deps -T schedule-import btp-schedule activate
<previous id>`.

**A failed import doesn't stop a deploy.** Each deploy reruns `schedule-import`
(`import --activate`). A staged version is no longer a failure: it stays `ready`. If the
download or import does fail, `schedule-import` keeps the active version and exits 0
(`deploy/compose/compose.release.yaml`). The deploy goes on, and the workflow run shows the
warning "schedule-import failed". Check the reason with `dc logs schedule-import`. Only a server
with no active version at all (a new instance) fails the deploy at this step.

## If the instance is replaced

Postgres and Kafka live on the instance's root volume, which is deleted with it. Until nightly
backups exist (#28, M3), **replacing the instance loses all collected data**. The release
README explains what prevents an accidental replacement. After a deliberate rebuild:

1. Apply Terraform; cloud-init installs Docker and Compose
   ([release README](../deploy/terraform/release/README.md#check-the-first-boot)).
2. If the instance ID changed, update the `RELEASE_INSTANCE_ID` secret.
3. Run `gh workflow run release.yml`. The first deploy clones the repository to
   `/opt/btp/repo`, and the first live schedule import takes several minutes.

## Shut down

Follow the [shutdown and archive plan](cost.md#shutdown-and-archive-plan). Before destroying
anything, disable the release workflow so a push doesn't deploy to a server that is gone:
`gh workflow disable release.yml`.
