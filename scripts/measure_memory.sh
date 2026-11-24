#!/usr/bin/env bash
# Record the memory and CPU of every container of one compose project until stopped.
#
#   scripts/measure_memory.sh OUTPUT_CSV [INTERVAL_S] [PROJECT]
#
# INTERVAL_S defaults to 30, PROJECT to btp-live (the live stack locally and on the release
# server). Every interval appends one row per running container to OUTPUT_CSV:
#
#   timestamp,container,mem_bytes,mem_limit_bytes,cpu_percent
#
# The header is written only when the file is new, so a restarted run appends. On Ctrl-C or
# SIGTERM it writes OUTPUT_CSV minus .csv plus .summary.txt: each container's cgroup
# memory.peak and OOMKilled flag. Summarise with scripts/summarise_memory.py.
#
# mem_bytes is what `docker stats` reports: on cgroup v2 that is memory.current minus
# inactive page cache. memory.peak counts the page cache too, so it can be higher.
set -euo pipefail

usage() {
  echo "usage: $0 OUTPUT_CSV [INTERVAL_S=30] [PROJECT=btp-live]" >&2
  exit 2
}

[ $# -ge 1 ] && [ $# -le 3 ] || usage
csv=$1
interval=${2:-30}
project=${3:-btp-live}
summary="${csv%.csv}.summary.txt"

command -v docker >/dev/null || { echo "measure: needs docker on PATH" >&2; exit 1; }
[[ "$interval" =~ ^[1-9][0-9]*$ ]] || { echo "measure: INTERVAL_S must be a positive integer" >&2; exit 2; }

filter="label=com.docker.compose.project=$project"

# Container IDs of the project; running only unless "all" is passed.
containers() {
  if [ "${1:-}" = all ]; then
    docker ps -aq --filter "$filter"
  else
    docker ps -q --filter "$filter"
  fi
}

# One `docker stats` reading of the running containers, as CSV rows with a UTC timestamp.
# MemUsage looks like "45.3MiB / 256MiB"; awk turns both sides into bytes.
sample() {
  local ids
  ids=$(containers)
  [ -n "$ids" ] || { echo "measure: no running containers in project $project" >&2; return 0; }
  # shellcheck disable=SC2086 # one argument per container ID
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $ids |
    awk -F'\t' -v ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
      function bytes(s,   n, u) {
        gsub(/ /, "", s)
        n = s; sub(/[A-Za-z]+$/, "", n)
        u = s; sub(/^[0-9.]+/, "", u)
        if (u == "B") return n
        if (u == "KiB") return n * 1024
        if (u == "MiB") return n * 1024 ^ 2
        if (u == "GiB") return n * 1024 ^ 3
        if (u == "TiB") return n * 1024 ^ 4
        if (u == "kB" || u == "KB") return n * 1000
        if (u == "MB") return n * 1000 ^ 2
        if (u == "GB") return n * 1000 ^ 3
        return -1
      }
      {
        split($2, mem, "/")
        cpu = $3; sub(/%$/, "", cpu)
        printf "%s,%s,%.0f,%.0f,%s\n", ts, $1, bytes(mem[1]), bytes(mem[2]), cpu
      }'
}

# Per container: memory.peak from its cgroup (needs `cat` in the image and a running
# container) and the OOMKilled flag.
write_summary() {
  local id name state oom peak
  {
    echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ) project=$project"
    echo "# container memory_peak_bytes oom_killed note"
    for id in $(containers all); do
      read -r name state oom < <(docker inspect -f '{{.Name}} {{.State.Status}} {{.State.OOMKilled}}' "$id")
      name=${name#/}
      if [ "$state" != running ]; then
        echo "$name - $oom skipped: $state"
      elif peak=$(docker exec "$id" cat /sys/fs/cgroup/memory.peak 2>/dev/null); then
        echo "$name $peak $oom"
      else
        echo "$name - $oom skipped: cannot read memory.peak (no cat, or kernel < 5.19)"
      fi
    done
  } >"$summary"
  echo "measure: summary in $summary" >&2
}

stop() {
  trap - INT TERM
  [ -n "${sleeper:-}" ] && kill "$sleeper" 2>/dev/null || true
  write_summary
  exit 0
}
trap stop INT TERM

[ -s "$csv" ] || echo "timestamp,container,mem_bytes,mem_limit_bytes,cpu_percent" >"$csv"
echo "measure: project $project every ${interval}s into $csv (Ctrl-C to stop)" >&2

while true; do
  # A failed reading (container restarting, daemon busy) is skipped, not fatal.
  sample >>"$csv" || echo "measure: reading failed at $(date -u +%H:%M:%SZ), skipped" >&2
  # Sleep in the background so a signal interrupts `wait` at once.
  sleep "$interval" &
  sleeper=$!
  wait "$sleeper" || true
done
