#!/usr/bin/env bash
# usage: browser-pool.sh <run> acquire <lane> <holder-pid> [any|remote|local]   -> prints the slot: remote:N or local:N
#        browser-pool.sh <run> release <lane>
#        browser-pool.sh <run> list
# Browser slots for web characters and their extra browsers, from BROWSER_SLOTS in config.env
# ("remote:1 remote:2 remote:3" = headless Chromium on REMOTE_HOST; "local:1 local:2" = agent-browser here).
# The config is re-read on every try, so shrinking BROWSER_SLOTS applies to the next start without a restart.
# A slot whose holder pid is dead is free. A slot of a kind is granted only while fewer browsers of that kind are
# live than BROWSER_SLOTS lists: holders of slots since removed still count until their day ends.
# Remote slots also need room on REMOTE_HOST when the gates are set: MemAvailable >= REMOTE_MIN_FREE_MB and
# 1-min load < cores x REMOTE_MAX_LOAD_PER_CORE (empty = no gate). A remote slot whose CDP port
# (REMOTE_CDP_BASE + N) already listens here and is not this run's own forward belongs to another session and is
# skipped. Every wait is logged to capacity.log once per reason.
set -u
RUN="$1"; CMD="$2"; LANE="${3:-}"
D="$RUN/.browsers"; mkdir -p "$D"
alive() { [ -f "$1" ] && kill -0 "$(awk '{print $2}' "$1")" 2>/dev/null; }
live() { local n=0 f; for f in "$D/$1"-*; do alive "$f" && n=$((n+1)); done; echo "$n"; }
listening() { python3 -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0 else 1)" "$1"; }
ours() { [ -f "$D/port-$1.pid" ] && kill -0 "$(cat "$D/port-$1.pid")" 2>/dev/null; }
remote_room() {  # prints nothing when there is room, else the reason
  [ -z "${REMOTE_MIN_FREE_MB:-}" ] && [ -z "${REMOTE_MAX_LOAD_PER_CORE:-}" ] && return
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" \
    'echo $(nproc) $(cut -d" " -f1 /proc/loadavg) $(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo)' 2>/dev/null |
    awk -v m="${REMOTE_MIN_FREE_MB:-}" -v l="${REMOTE_MAX_LOAD_PER_CORE:-}" '
      NF<3 {print "remote unreachable"; exit}
      m != "" && $3 < m+0 {print "remote free " $3 "MB < " m "MB"; exit}
      l != "" && $2 >= $1*l {print "remote load " $2 " >= " $1 "x" l; exit}
      {exit}' || echo "remote unreachable"
}
log() { echo "$(date +%H:%M:%S) [$LANE] $1" >> "$RUN/capacity.log"; }

case "$CMD" in
acquire)
  PID="$4"; PREF="${5:-any}"; last=""
  while :; do
    . "$RUN/config.env"
    slots="${BROWSER_SLOTS:-}"
    nr=$(echo "$slots" | tr ' ' '\n' | grep -c '^remote:'); nl=$(echo "$slots" | tr ' ' '\n' | grep -c '^local:')
    case "$PREF" in
      remote) order=$(echo "$slots" | tr ' ' '\n' | grep '^remote:') ;;
      local) order=$(echo "$slots" | tr ' ' '\n' | grep '^local:') ;;
      *) order=$(echo "$slots" | tr ' ' '\n' | grep -E '^(remote|local):' | sort -t: -k1,1r -s) ;;
    esac
    why=""; room="unchecked"
    if [ -z "$order" ]; then why="no $PREF slot in BROWSER_SLOTS"
    elif mkdir "$D/.acquire.lock" 2>/dev/null && echo $$ > "$D/.acquire.lock/pid"; then
      for s in $order; do
        f="$D/${s/:/-}"; alive "$f" && continue
        case "$s" in
          remote:*)
            [ "$(live remote)" -ge "$nr" ] && { why="all $nr remote browsers busy"; continue; }
            port=$(( ${REMOTE_CDP_BASE:-9320} + ${s#remote:} ))
            if listening "$port" && ! ours "$port"; then
              why="port $port already listens here (another session); slot $s skipped"; log "SKIP $why"; continue
            fi
            [ "$room" = unchecked ] && room=$(remote_room)
            [ -n "$room" ] && { why="$room"; continue; } ;;
          local:*)
            [ "$(live local)" -ge "$nl" ] && { why="all $nl local browsers busy"; continue; } ;;
        esac
        echo "$LANE $PID $(date +%s)" > "$f"; rm -rf "$D/.acquire.lock"
        log "BROWSER $s"; echo "$s"; exit 0
      done
      rm -rf "$D/.acquire.lock"
      [ -z "$why" ] && why="every slot busy"
    else
      # a lock left by a killed acquirer must not stall the pool
      kill -0 "$(cat "$D/.acquire.lock/pid" 2>/dev/null)" 2>/dev/null || rm -rf "$D/.acquire.lock"
      why="pool lock busy"
    fi
    [ "$why" != "$last" ] && log "WAIT browser: $why"; last="$why"
    sleep "${BROWSER_POLL_S:-30}"
  done ;;
release)
  for f in "$D"/local-* "$D"/remote-*; do
    [ -f "$f" ] && [ "$(awk '{print $1}' "$f")" = "$LANE" ] && rm -f "$f"
  done; exit 0 ;;
list)
  . "$RUN/config.env"
  for f in "$D"/local-* "$D"/remote-*; do
    [ -f "$f" ] || continue
    s=$(basename "$f"); s="${s/-/:}"
    case " ${BROWSER_SLOTS:-} " in *" $s "*) note="" ;; *) note=" (removed from BROWSER_SLOTS)" ;; esac
    if alive "$f"; then echo "$s $(awk '{print $1}' "$f")$note"; else echo "$s free (dead holder)$note"; fi
  done ;;
*) echo "usage: browser-pool.sh <run> acquire <lane> <pid> [any|remote|local] | release <lane> | list" >&2; exit 2 ;;
esac
