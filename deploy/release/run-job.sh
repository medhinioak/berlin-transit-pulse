#!/usr/bin/env bash
# Runs one batch job on the release server as a one-shot container of the deployed release.
# Called by the systemd units in deploy/release/systemd/ (ADR-0011):
#   /opt/btp/repo/deploy/release/run-job.sh <job name> <compose service> <command…>
# e.g. run-job.sh schedule-refresh schedule-import btp-schedule refresh
# Uses the images of /opt/btp/deployed_sha (pulled by deploy.sh; never pulls) and the env
# file deploy.sh renders. Logs start, end, duration and exit code; systemd sends all output to
# the journal (journalctl -u btp-<job>). Exits with the job's exit code.
set -euo pipefail

BTP_DIR=/opt/btp
REPO="$BTP_DIR/repo"

log() {
  echo "run-job: $(date -u +%H:%M:%SZ) $*"
}

[ "$#" -ge 3 ] || { echo "usage: run-job.sh <job name> <compose service> <command…>" >&2; exit 2; }
name=$1
service=$2
shift 2

sha=$(cat "$BTP_DIR/deployed_sha")
export BTP_IMAGE_TAG=$sha
COMPOSE=(docker compose -p btp-live --env-file "$BTP_DIR/.env"
  -f "$REPO/deploy/compose/compose.yaml" -f "$REPO/deploy/compose/compose.release.yaml")

log "$name: start (release $sha): $*"
started=$SECONDS
code=0
"${COMPOSE[@]}" run --rm --no-deps --pull never -T "$service" "$@" || code=$?
log "$name: end: exit $code after $((SECONDS - started)) s"
exit "$code"
