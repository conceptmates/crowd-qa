#!/usr/bin/env bash
# usage: resume.sh <run-dir>
# Continues a run from disk, in any session or after a reboot:
#   1. restarts the watchers (restart-watchers.sh) and runs the watchdog once (stack up, data restored)
#   2. runs the stack check and saves state/
#   3. starts crowd.py again unless it is still running. crowd.py reads everything it needs from disk: finished
#      days are skipped, judged days are not judged again, live runners are waited for, never started twice.
set -u
RUN="$(cd "${1:-$(dirname "$0")/..}" && pwd)"
. "$RUN/config.env"
echo "== watchers"; bash "$RUN/scripts/restart-watchers.sh" "$RUN"
if [ -n "${STACK_UP_CMD:-}" ] || [ -n "${DB_BACKUP_CMD:-}" ]; then echo "== watchdog"; bash "$RUN/scripts/watchdog.sh" "$RUN" | tail -2; fi
if [ -n "${STACK_VERIFY:-}" ]; then echo "== stack check"; bash -c "$STACK_VERIFY" 2>&1 | tail -1; fi
echo "== state"; python3 "$RUN/scripts/save-state.py" "$RUN"
echo "== runners still alive (crowd.py waits for them)"; pgrep -fl "run-tester.sh $RUN " || echo none
pid=$(cat "$RUN/logs/crowd.pid" 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  echo "== crowd.py already running (pid $pid)"
else
  mkdir -p "$RUN/logs"
  nohup python3 "$RUN/scripts/crowd.py" "$RUN" all >> "$RUN/logs/crowd.out" 2>&1 &
  echo "== crowd.py started (pid $!): tail -f $RUN/logs/crowd.log"
fi
