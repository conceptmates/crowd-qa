#!/usr/bin/env bash
# usage: restart-watchers.sh <run-dir>
# Safe to run any time, and the first thing to run after a reboot. Starts whatever is not already running:
#   memlog.sh       memory, swap and load every minute (memlog.txt)
#   api-watch.sh    stack health every minute; writes STACK_DOWN so testers hold (only when HEALTH_CHECKS is set)
#   state-saver.sh  state/ on disk every 5 minutes, so any new session can resume the run
#   square-view.py  the live square page (square/index.html)
#   tunnel.sh       remote mode only
#   DEV_CMD         when set and APP_URL does not answer
RUN="$1"
. "$RUN/config.env"
mkdir -p "$RUN/logs"
start() {  # start <name> <pattern> <command...>
  local name="$1" pat="$2"; shift 2
  if pgrep -f "$pat" >/dev/null; then echo "$name: running"
  else nohup "$@" >/dev/null 2>&1 & echo "$name: started"; fi
}

boot=$(sysctl -n kern.boottime 2>/dev/null | sed -n 's/^{ sec = \([0-9]*\),.*/\1/p')
[ -z "$boot" ] && boot=$(( $(date +%s) - $(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0) ))
last=$(tail -1 "$RUN/memlog.txt" 2>/dev/null)
if [ -n "$last" ] && [ "$(stat -f %m "$RUN/memlog.txt" 2>/dev/null || stat -c %Y "$RUN/memlog.txt")" -lt "$boot" ]; then
  echo "rebooted since the last memlog line: $last"
fi

start memlog "memlog.sh $RUN" bash "$RUN/scripts/memlog.sh" "$RUN"
start state-saver "state-saver.sh $RUN" bash "$RUN/scripts/state-saver.sh" "$RUN"
start square-page "square-view.py $RUN" python3 "$RUN/scripts/square-view.py" "$RUN" --watch
if [ -n "${HEALTH_CHECKS:-}" ]; then start api-watch "api-watch.sh $RUN" bash "$RUN/scripts/api-watch.sh" "$RUN"
else echo "api-watch: off (set HEALTH_CHECKS in config.env)"; fi
[ "${STACK_MODE:-local}" = remote ] && start tunnel "tunnel.sh $RUN" bash "$RUN/scripts/tunnel.sh" "$RUN"

if [ -n "${DEV_CMD:-}" ] && [ -n "${APP_URL:-}" ]; then
  if curl -s -o /dev/null -m 5 "$APP_URL"; then echo "dev server: answering at $APP_URL"
  else
    (cd "${DEV_DIR:-.}" && nohup sh -c "$DEV_CMD" > "$RUN/logs/devserver.log" 2>&1 &)
    for _ in $(seq 1 40); do curl -s -o /dev/null -m 3 "$APP_URL" && break; sleep 3; done
    curl -s -o /dev/null -m 3 "$APP_URL" && echo "dev server: started" || echo "dev server: NOT answering, see logs/devserver.log"
  fi
fi
case "${TARGET:-web}" in ios|android) echo "mobile: boot the devices in DEVICE_POOL and install the app before resuming" ;; esac

# testers still alive after an orchestrator restart (not after a reboot) are left alone; day agents re-attach
echo "testers running: $(pgrep -f "run-tester.sh $RUN " | wc -l | tr -d ' ')"
