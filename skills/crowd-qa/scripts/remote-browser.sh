#!/usr/bin/env bash
# usage: remote-browser.sh <run> start <name> <remote:N> [tz]   -> prints the CDP port for agent-browser session crowd-<name>
#        remote-browser.sh <run> stop <name>
# start: runs remote-chrome.sh on REMOTE_HOST (CDP on REMOTE_CDP_BASE + N), forwards that port here with ssh -L,
# waits until the forwarded port answers (a loaded machine took more than 3 s to open a forward, and the first
# command then failed), and opens the session once through it. Callers export AGENT_BROWSER_CDP=<port> and
# AGENT_BROWSER_EXECUTABLE_PATH=scripts/no-local-browser.sh, never `agent-browser connect`: connect started an idle
# local Chrome per session and fell back to it when the link dropped.
# A port that already listens here and is not this run's own forward is refused: it belongs to another session
# (another crowd run, a fix agent), and connecting would drive somebody else's browser.
# Exit codes: 3 port busy here, 4 remote Chromium failed, 5 forward never answered, 6 session did not open.
set -u
RUN="$1"; CMD="$2"; NAME="$3"
. "$RUN/config.env"
D="$RUN/.browsers"; mkdir -p "$D"
RC="${REMOTE_CHROME:-~/crowd-qa/remote-chrome.sh}"
NOLOCAL="$RUN/scripts/no-local-browser.sh"
listening() { python3 -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0 else 1)" "$1"; }
stop() {
  local port; port=$(cat "$D/cdp-$NAME" 2>/dev/null)
  [ -n "$port" ] && AGENT_BROWSER_CDP="$port" AGENT_BROWSER_EXECUTABLE_PATH="$NOLOCAL" AGENT_BROWSER_SESSION="crowd-$NAME" \
    agent-browser close >/dev/null 2>&1
  [ -f "$D/fwd-$NAME.pid" ] && kill "$(cat "$D/fwd-$NAME.pid")" 2>/dev/null
  [ -n "$port" ] && rm -f "$D/port-$port.pid"
  rm -f "$D/fwd-$NAME.pid" "$D/cdp-$NAME"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "$RC stop $NAME" >/dev/null 2>&1
}
case "$CMD" in
start)
  SLOT="$4"; ZONE="${5:-}"; PORT=$(( ${REMOTE_CDP_BASE:-9320} + ${SLOT#remote:} ))
  # a forward this run left behind for the same port (its holder died) is ours to end
  [ -f "$D/port-$PORT.pid" ] && kill "$(cat "$D/port-$PORT.pid")" 2>/dev/null && sleep 1
  if listening "$PORT"; then echo "port $PORT already listens here (another session); refusing to forward it" >&2; exit 3; fi
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "$RC start $NAME $PORT $ZONE" >&2 \
    || { echo "remote-chrome.sh start failed on $REMOTE_HOST" >&2; exit 4; }
  ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -L "$PORT:127.0.0.1:$PORT" "$REMOTE_HOST" >/dev/null 2>&1 < /dev/null &
  FWD=$!; echo "$FWD" > "$D/fwd-$NAME.pid"; echo "$FWD" > "$D/port-$PORT.pid"; echo "$PORT" > "$D/cdp-$NAME"
  ok=""
  for _ in $(seq 1 "${CDP_WAIT_S:-60}"); do
    curl -sf -m2 "http://127.0.0.1:$PORT/json/version" >/dev/null && { ok=1; break; }
    kill -0 "$FWD" 2>/dev/null || break
    sleep 1
  done
  [ -z "$ok" ] && { echo "the forward of port $PORT never answered" >&2; stop; exit 5; }
  AGENT_BROWSER_CDP="$PORT" AGENT_BROWSER_EXECUTABLE_PATH="$NOLOCAL" AGENT_BROWSER_SESSION="crowd-$NAME" \
    agent-browser open about:blank >&2 || { echo "agent-browser could not open crowd-$NAME through port $PORT" >&2; stop; exit 6; }
  echo "$PORT" ;;
stop) stop ;;
*) echo "usage: remote-browser.sh <run> start <name> <remote:N> [tz] | stop <name>" >&2; exit 2 ;;
esac
