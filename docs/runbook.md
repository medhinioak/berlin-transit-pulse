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

## Database

```bash
dc exec postgres psql -U postgres -d btp
```

The Postgres roles get their passwords from the env file **on the first start only**
(`postgres/init/`). A new value in SSM reaches `/opt/btp/.env` on the next deploy, but the role
keeps the old password and the service then fails to log in. To rotate one, set the new password
with `ALTER ROLE … PASSWORD …` in psql, then redeploy.

## 13 December 2026: the timetable change

VBB's annual timetable starts on Sunday 2026-12-13, with new trip IDs. Until the weekly GTFS
refresh (#24) is merged and deployed, the new static GTFS has to be imported by hand **that
morning**:

```bash
# in a shell on the server (see above). Max age 0: ignore the cached zip, which is the old
# timetable and would make the import a no-op.
dc run --rm -e BTP_SCHEDULE_MAX_AGE_HOURS=0 schedule-import
dc exec postgres psql -U postgres -d btp -c \
  "select version_id, status, feed_start_date, feed_end_date, activated_at
     from schedule.versions order by created_at"
```

The new version should be `active` with `feed_start_date` 2026-12-13, and the old one `retired`.

Then check from your machine that live data still matches the new schedule:

```bash
curl -s "https://$(terraform -chdir=deploy/terraform/release output -raw cloudfront_domain_name)/api/v1/now/by-mode" | jq
```

Every mode should still show `running_trips > 0`. If it doesn't, roll back to the previous
version (see the schedule README, `btp-schedule activate <id>`) and investigate.

**A failed import doesn't stop a deploy.** Each deploy reruns `schedule-import`. Once VBB
publishes the new timetable, its zip starts on the 13th, so it doesn't cover today and fails
validation (see "Staging early" in [`services/schedule/README.md`](../services/schedule/README.md#known-limitations-for-24-weekly-refresh)).
In the release, `schedule-import` then keeps the active version and exits 0
(`deploy/compose/compose.release.yaml`). The deploy goes on, and the workflow run shows a warning
"schedule-import failed". The same happens when the download fails. Check the reason with
`dc logs schedule-import`. Only a server with no active version at all (a new instance) fails
the deploy at this step.

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
