#!/usr/bin/env bash
# api-watch.sh <run>: every 60 s, runs each check in HEALTH_CHECKS (config.env). Two failing rounds in a row
# write <run>/STACK_DOWN: runners do not start a character while it exists, testers are told to hold and record
# nothing, and the judge treats the window as a stack fault. It also posts a notice on the square and runs
# STACK_UP_CMD / the watchdog when set. On recovery it removes STACK_DOWN, posts again and records the window
# in stack-faults.md. Stop: kill $(cat <run>/logs/api-watch.pid)
#
# HEALTH_CHECKS = "name|command; name|command; ..."   each command must exit 0 when healthy. Examples:
#   api|curl -sf -m5 http://localhost:4100/health
#   canary|curl -sf -m8 -X POST localhost:4100/api/login -H 'content-type: application/json' -d @canary.json
# A canary sign-in matters: it catches a wiped database, which a health endpoint never does.
#
# api-watch.sh <run> once: run every check one time and exit 0 when all pass, 1 (printing the failing names) when
# not. run-tester.sh calls it right before a character starts, so a dead stack holds the crowd even when this
# watcher is not running (after a reboot it was not, and testers started against a dead server for 51 minutes).
#
# NOTIFY_CMD (config.env, optional) runs with one argument, the notice text, when the stack goes down and when it
# comes back: a desktop notification, a chat webhook, or a file the orchestrating session watches.
RUN="$1"; mkdir -p "$RUN/logs"
LOG="$RUN/logs/api-watch.log"
fails=0; down_since=""

check() {
  . "$RUN/config.env"
  local why="" IFS=';' item name cmd
  for item in $HEALTH_CHECKS; do
    name="${item%%|*}"; cmd="${item#*|}"; name="$(echo "$name" | xargs)"
    [ -z "$cmd" ] && continue
    bash -c "$cmd" >/dev/null 2>&1 || why="$why $name"
  done
  echo "$why"
}
notify() { . "$RUN/config.env"; [ -n "${NOTIFY_CMD:-}" ] && bash -c "$NOTIFY_CMD \"\$1\"" _ "$1" >/dev/null 2>&1; }

if [ "${2:-}" = once ]; then
  why=$(check); [ -z "$why" ] && exit 0; echo "failing:$why"; exit 1
fi
echo $$ > "$RUN/logs/api-watch.pid"

while :; do
  why=$(check)
  if [ -z "$why" ]; then
    if [ -f "$RUN/STACK_DOWN" ]; then
      rm -f "$RUN/STACK_DOWN"
      echo "$(date '+%F %T') RECOVERED (down since $down_since)" >> "$LOG"
      python3 "$RUN/scripts/square.py" "$RUN" post organiser "NOTICE: the test servers are back ($(date +%H:%M)). Anything that failed between $down_since and now was the outage, not the product. Carry on where you stopped." >/dev/null 2>&1
      echo "- **Stack outage ${down_since:-?} to $(date +%H:%M)** (api-watch.sh): every finding in that window is a stack fault." >> "$RUN/stack-faults.md"
      notify "crowd-qa: test servers back at $(date +%H:%M) (down since $down_since)"
    fi
    fails=0
  else
    fails=$((fails+1))
    echo "$(date '+%F %T') FAIL$why (x$fails)" >> "$LOG"
    if [ "$fails" -ge 2 ] && [ ! -f "$RUN/STACK_DOWN" ]; then
      down_since=$(date +%H:%M)
      echo "since=$down_since reason=$why" > "$RUN/STACK_DOWN"
      python3 "$RUN/scripts/square.py" "$RUN" post organiser "NOTICE: the test servers are down ($down_since:$why). Stop testing and don't report what you see now; wait until STACK_DOWN is gone, then continue." >/dev/null 2>&1
      notify "crowd-qa: test servers DOWN since $down_since:$why"
      . "$RUN/config.env"
      if [ -n "${STACK_UP_CMD:-}" ]; then
        (nohup bash "$RUN/scripts/watchdog.sh" "$RUN" >> "$RUN/logs/watchdog.log" 2>&1 &)
      fi
    fi
  fi
  sleep 60
done
