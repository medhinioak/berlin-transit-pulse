#!/usr/bin/env bash
# End-to-end smoke test: start the stack offline, check the API and the web page, run any
# extra command while the stack is up (CI: `scripts/smoke.sh make test-integration`), then
# stop the stack and remove its volumes.
#
# The 180 s limit starts after `make up` returns; CI bounds `make up` with the step timeout.
#
# Runs as its own compose project (default btp-smoke), so `down -v` never touches the volumes
# of a local `make up` stack. Stop that stack first (`make down`): both use the same ports.
set -euo pipefail

cd "$(dirname "$0")/.."

for tool in docker make curl jq; do
  command -v "$tool" >/dev/null || { echo "smoke: needs $tool on PATH" >&2; exit 1; }
done

export BTP_FEED_MODE=fixtures
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-btp-smoke}"
COMPOSE=(docker compose -f deploy/compose/compose.yaml)
API=http://localhost:8000
WEB=http://localhost:8080
TIMEOUT_S=180

fail() {
  echo "smoke: FAIL: $*" >&2
  exit 1
}

cleanup() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    echo "smoke: logs of the failed run" >&2
    "${COMPOSE[@]}" logs --tail=200 >&2 || true
  fi
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$code"
}
trap cleanup EXIT

echo "smoke: starting the stack ($COMPOSE_PROJECT_NAME, fixtures mode)"
make up

echo "smoke: waiting for $API/health"
deadline=$((SECONDS + TIMEOUT_S))
until [ "$(curl -s -o /dev/null -w '%{http_code}' "$API/health")" = 200 ]; do
  [ "$SECONDS" -lt "$deadline" ] || fail "/health not 200 after ${TIMEOUT_S}s"
  sleep 2
done

# The first replayed snapshot reaches the API a few seconds after the processor commits it.
echo "smoke: waiting for at least two modes with running trips"
until
  running=$(curl -sf "$API/v1/now/by-mode" | jq '[.modes[] | select(.running_trips > 0)] | length') &&
    [ "$running" -ge 2 ]
do
  [ "$SECONDS" -lt "$deadline" ] || fail "/v1/now/by-mode has ${running:-no} modes with running trips"
  sleep 2
done
echo "smoke: $running modes with running trips"

page=$(curl -sf "$WEB/") || fail "$WEB/ did not return 200"
grep -q "VBB" <<<"$page" || fail "$WEB/ has no VBB attribution"
grep -q "CC-BY 4.0" <<<"$page" || fail "$WEB/ has no CC-BY 4.0 attribution"
echo "smoke: web page served with attribution"

if [ "$#" -gt 0 ]; then
  echo "smoke: running '$*' against the stack"
  "$@"
fi

echo "smoke: OK"
