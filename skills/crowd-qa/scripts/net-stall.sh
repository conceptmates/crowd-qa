#!/usr/bin/env bash
# usage: net-stall.sh <run-dir> stall <seconds, max 90> | resume
# Fakes a weak signal for an exclusive character: freezes (SIGSTOP) the process the app's traffic goes
# through, then always thaws it (SIGCONT) after the given seconds. Requests hang the way they do on a bad
# network. What gets frozen depends on where the backend runs (config.env STACK_MODE):
#   remote: the SSH tunnel's ssh process (logs/tunnel-ssh.pid, written by tunnel.sh)
#   local : whatever listens on API_PORT (default 3005)
# Only an exclusive character may call it: everyone else is waiting while it runs.
set -u
RUN="$1"; MODE="$2"; SECS="${3:-30}"
. "$RUN/config.env"
if [ "${STACK_MODE:-local}" = remote ]; then
  pids=$(cat "$RUN/logs/tunnel-ssh.pid" 2>/dev/null)
else
  pids=$(lsof -tiTCP:"${API_PORT:-3005}" -sTCP:LISTEN 2>/dev/null)
fi
[ -z "$pids" ] && { echo "nothing to freeze (STACK_MODE=${STACK_MODE:-local})" >&2; exit 1; }
case "$MODE" in
stall)
  [ -f "$RUN/.exclusive" ] || { echo "refused: only an exclusive character may stall the network" >&2; exit 1; }
  [ "$SECS" -gt 90 ] && SECS=90
  kill -STOP $pids; echo "network frozen for ${SECS}s"
  ( sleep "$SECS"; kill -CONT $pids ) >/dev/null 2>&1 &
  ;;
resume) kill -CONT $pids; echo "network thawed" ;;
*) echo "usage: net-stall.sh <run> stall <s> | resume" >&2; exit 2 ;;
esac
