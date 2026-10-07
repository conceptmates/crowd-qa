#!/usr/bin/env bash
# usage: capacity.sh <run-dir> check|wait [lane] [holder-pid]
# check: exit 0 if one more lane fits now, 1 if not (prints the reason).
# wait:  block until one more lane fits, rechecking every 60s; then record the lane as active
#        (.active/<lane> holding holder-pid) under a lock so two lanes never take the same slot.
#        Logs each decision to capacity.log.
# A lane fits when: running testers < HARD_CAP, free memory (free + inactive + purgeable) >= LANE_RAM_MB,
# free-memory % >= MIN_FREE_PCT, swap did not grow > 1 GB in the last 5 min, 1-min load < cores * MAX_LOAD_PER_CORE.
set -u
RUN="$1"; MODE="${2:-check}"; LANE="${3:-?}"; HOLDER="${4:-$PPID}"
mkdir -p "$RUN/.active"
. "$RUN/config.env"

fits() {
  local running cores load free_mb free_pct swap_mb swap_prev grow
  running=0
  for f in "$RUN"/.active/*; do [ -f "$f" ] || continue; if kill -0 "$(cat "$f")" 2>/dev/null; then running=$((running+1)); else rm -f "$f"; fi; done
  if [ "$running" -ge "$HARD_CAP" ]; then REASON="at hard cap ($running/$HARD_CAP)"; return 1; fi
  if [ "$(uname)" = Darwin ]; then
    cores=$(sysctl -n hw.ncpu)
    load=$(sysctl -n vm.loadavg | awk '{print $2}')
    local ps; ps=$(pagesize)
    free_mb=$(vm_stat | awk -v ps="$ps" '/Pages free|Pages inactive|Pages purgeable/{gsub("\\.","",$NF); s+=$NF} END{print int(s*ps/1048576)}')
    free_pct=$(memory_pressure 2>/dev/null | awk '/free percentage/{gsub("%","",$5); print $5}')
    swap_mb=$(sysctl -n vm.swapusage | awk '{gsub("M","",$6); print int($6)}')
  else
    cores=$(nproc); load=$(awk '{print $1}' /proc/loadavg)
    free_mb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    free_pct=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print int(a*100/t)}' /proc/meminfo)
    swap_mb=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print int((t-f)/1024)}' /proc/meminfo)
  fi
  free_pct=${free_pct:-100}
  # lanes admitted in the last 3 min have not loaded their browser/emulator yet: reserve their RAM
  local fresh; fresh=$(find "$RUN/.active" -type f -mmin -3 2>/dev/null | wc -l | tr -d ' ')
  free_mb=$(( free_mb - fresh * LANE_RAM_MB ))
  # swap growth over the last 5 minutes, from the sample file
  local S="$RUN/.swap-samples"; echo "$(date +%s) $swap_mb" >> "$S"; tail -20 "$S" > "$S.t" && mv "$S.t" "$S"
  swap_prev=$(awk -v now="$(date +%s)" '$1>=now-330{print $2; exit}' "$S"); grow=$(( swap_mb - ${swap_prev:-$swap_mb} ))
  if [ "$free_mb" -lt "$LANE_RAM_MB" ]; then REASON="free ${free_mb}MB < ${LANE_RAM_MB}MB per lane"; return 1; fi
  if [ "$free_pct" -lt "$MIN_FREE_PCT" ]; then REASON="free ${free_pct}% < ${MIN_FREE_PCT}%"; return 1; fi
  if [ "$grow" -gt 1024 ]; then REASON="swap grew ${grow}MB in 5 min"; return 1; fi
  if python3 -c "import sys;sys.exit(0 if $load >= $cores*$MAX_LOAD_PER_CORE else 1)"; then REASON="load $load >= ${cores}x$MAX_LOAD_PER_CORE"; return 1; fi
  REASON="ok: running=$running free=${free_mb}MB/${free_pct}% swap=${swap_mb}MB(+${grow}) load=$load"
  return 0
}

log() { echo "$(date +%H:%M:%S) [$LANE] $1" >> "$RUN/capacity.log"; }

if [ "$MODE" = check ]; then fits; rc=$?; echo "$REASON"; exit $rc; fi
first=1; REASON="admission lock busy"
while :; do
  if mkdir "$RUN/.admit.lock" 2>/dev/null; then
    if fits; then echo "$HOLDER" > "$RUN/.active/$LANE"; rmdir "$RUN/.admit.lock"; log "ADMIT $REASON"; exit 0; fi
    rmdir "$RUN/.admit.lock"
  fi
  [ $first = 1 ] && log "WAIT $REASON"; first=0
  sleep 60
done
