#!/usr/bin/env bash
# Runs a shell script on the release instance through SSM Run Command (AWS-RunShellScript),
# waits for it, prints its output and exits non-zero unless it succeeded.
# Used by .github/workflows/release.yml; works the same from a maintainer's machine:
#   AWS_PROFILE=btp deploy/release/ssm-run.sh <instance-id> <timeout-s> <script-file>
# Needs the AWS CLI and jq, and credentials allowed to send commands to the instance.
# SSM returns at most 24,000 characters of stdout and 8,000 of stderr; longer output is cut.
set -euo pipefail

[ "$#" -eq 3 ] || { echo "usage: ssm-run.sh <instance-id> <timeout-s> <script-file>" >&2; exit 2; }
instance=$1
timeout_s=$2
script=$3

# One command per line, as AWS-RunShellScript expects; it runs them as one script as root.
params=$(jq -Rn --arg timeout "$timeout_s" '{commands: [inputs], executionTimeout: [$timeout]}' <"$script")

command_id=$(aws ssm send-command \
  --instance-ids "$instance" \
  --document-name AWS-RunShellScript \
  --comment "btp ${GITHUB_WORKFLOW:-manual} ${GITHUB_RUN_ID:-}" \
  --timeout-seconds 600 \
  --parameters "$params" \
  --query Command.CommandId --output text)
echo "ssm-run: command $command_id on $instance (up to ${timeout_s}s)"

# The invocation can take a few seconds to appear after send-command.
deadline=$((SECONDS + timeout_s + 120))
status=Pending
while :; do
  sleep 10
  status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in
    Pending | InProgress | Delayed | Cancelling) ;;
    *) break ;;
  esac
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "ssm-run: still $status after ${timeout_s}s" >&2
    exit 1
  fi
done

aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
  --query StandardOutputContent --output text
stderr=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
  --query StandardErrorContent --output text)
if [ -n "$stderr" ] && [ "$stderr" != None ]; then
  echo "--- stderr ---"
  echo "$stderr"
fi

echo "ssm-run: $status"
[ "$status" = Success ]
