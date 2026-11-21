#!/usr/bin/env bash
# Deploys one git SHA on the release server: renders /opt/btp/.env from SSM Parameter Store,
# pulls that SHA's images from GHCR, restarts the stack in live mode and waits until it is up.
#
# Run as root by SSM Run Command from .github/workflows/release.yml, after the workflow has
# checked out <sha> in /opt/btp/repo (so this is always the script of the version it deploys):
#   /opt/btp/repo/deploy/release/deploy.sh <sha>
# Idempotent: running it again for the same SHA changes nothing. Output goes to stdout, which
# SSM returns to the workflow log. Runbook: docs/runbook.md.
set -euo pipefail

BTP_DIR=/opt/btp
REPO="$BTP_DIR/repo"
ENV_FILE="$BTP_DIR/.env"
SHA_FILE="$BTP_DIR/deployed_sha"
PREV_FILE="$BTP_DIR/previous_sha"
SSM_PREFIX=/btp/release
IMAGE_PREFIX=ghcr.io/medhinioak/btp-
# Containers that run once and exit 0; the same list as ONESHOTS in the Makefile.
ONESHOTS=(kafka-init schedule-import metrics-jobs-apply)
export AWS_REGION="${AWS_REGION:-eu-north-1}"

log() {
  echo "deploy: $(date -u +%H:%M:%SZ) $*"
}

fail() {
  echo "deploy: FAIL: $*" >&2
  exit 1
}

[ "$#" -eq 1 ] || fail "usage: deploy.sh <40-character git sha>"
sha=$1
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "not a full git sha: $sha"
[ "$(id -u)" -eq 0 ] || fail "run as root (SSM Run Command does)"
head=$(git -C "$REPO" rev-parse HEAD)
[ "$head" = "$sha" ] || fail "$REPO is at $head, not $sha; check out the sha first"

log "deploying $sha"

# --- 1. Env file from Parameter Store (instance role) ---
# Parameter name below /btp/release -> variable in compose.yaml.
declare -A ENV_NAMES=(
  [pg/superuser]=BTP_PG_SUPERUSER_PASSWORD
  [pg/processor]=BTP_PG_PROCESSOR_PASSWORD
  [pg/schedule]=BTP_PG_SCHEDULE_PASSWORD
  [pg/metrics_jobs]=BTP_PG_METRICS_JOBS_PASSWORD
  [pg/api]=BTP_PG_API_PASSWORD
  [feed_contact]=BTP_FEED_CONTACT
)

log "rendering $ENV_FILE from $SSM_PREFIX"
params=$(aws ssm get-parameters-by-path --path "$SSM_PREFIX" --recursive --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text)
declare -A values=()
while IFS=$'\t' read -r name value; do
  [ -n "$name" ] || continue
  values[${name#"$SSM_PREFIX"/}]=$value
done <<<"$params"

tmp=$(umask 077 && mktemp "$BTP_DIR/.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
{
  echo "# Rendered by deploy/release/deploy.sh from SSM $SSM_PREFIX; edits are overwritten."
  for key in "${!ENV_NAMES[@]}"; do
    value=${values[$key]:-}
    [ -n "$value" ] || fail "SSM parameter $SSM_PREFIX/$key is missing or empty"
    # Single quotes keep the value literal for compose; so it must not contain one.
    [[ "$value" != *"'"* && "$value" != *$'\n'* ]] || fail "$SSM_PREFIX/$key contains a quote or newline"
    echo "${ENV_NAMES[$key]}='$value'"
  done
} >"$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"

# --- 2. Pull and start (project btp-live, as for BTP_FEED_MODE=live locally, #75) ---
export BTP_IMAGE_TAG=$sha
COMPOSE=(docker compose -p btp-live --env-file "$ENV_FILE"
  -f "$REPO/deploy/compose/compose.yaml" -f "$REPO/deploy/compose/compose.release.yaml")

log "pulling images for $sha"
"${COMPOSE[@]}" pull --quiet

# The same steps as `make up`, without --build (the server has no make and builds nothing).
# `up` waits for the one-shots that other services depend on; `docker wait` then checks their
# exit codes, and `up --wait` waits for the long-running services to be healthy.
log "starting the stack"
"${COMPOSE[@]}" up -d --no-build --remove-orphans

for s in "${ONESHOTS[@]}"; do
  code=$(docker wait "$("${COMPOSE[@]}" ps -aq "$s")")
  [ "$code" = 0 ] || fail "$s exited with $code; see: docker compose -p btp-live logs $s"
  log "$s exited 0"
done

# schedule-import exits 0 after a failed import if an active version exists
# (compose.release.yaml). Say so loudly: ssm-run.sh echoes this on the runner, where
# ::warning:: becomes an annotation on the workflow run.
if "${COMPOSE[@]}" logs --no-log-prefix schedule-import | grep -q BTP_SCHEDULE_IMPORT_FAILED; then
  echo "::warning::schedule-import failed; the previously active schedule version stays in use. See: docker compose -p btp-live logs schedule-import"
fi

mapfile -t long_running < <("${COMPOSE[@]}" config --services | grep -vxF "${ONESHOTS[@]/#/-e}")
log "waiting for ${long_running[*]}"
"${COMPOSE[@]}" up -d --wait --wait-timeout 300 --no-build --no-deps "${long_running[@]}"

# --- 3. Record the release and prune older images ---
# A redeploy of the same SHA keeps the release before it as the previous one.
current=$(cat "$SHA_FILE" 2>/dev/null || true)
if [ -n "$current" ] && [ "$current" != "$sha" ]; then
  echo "$current" >"$PREV_FILE"
fi
echo "$sha" >"$SHA_FILE"
prev=$(cat "$PREV_FILE" 2>/dev/null || true)
log "deployed $sha (previous: ${prev:-none})"

# Keep this release's images and the previous one's: a rollback to it then only checks the
# manifests (pull_policy: always) and downloads no layers.
docker image ls --format '{{.Repository}}:{{.Tag}}' --filter "reference=${IMAGE_PREFIX}*" |
  while read -r image; do
    tag=${image##*:}
    if [ "$tag" != "$sha" ] && [ "$tag" != "$prev" ]; then
      docker image rm "$image" >/dev/null || log "could not remove $image (still in use?)"
    fi
  done
docker image prune -f >/dev/null

"${COMPOSE[@]}" ps -a
log "OK"
