#!/usr/bin/env bash
# usage: tunnel.sh <run-dir>        (start detached: nohup bash tunnel.sh <run> >/dev/null 2>&1 &)
# For STACK_MODE=remote: the backend runs on another machine and this one keeps only orchestration and the
# simulator. Forwards each port in TUNNEL_PORTS from localhost here to localhost on REMOTE_HOST, so an app
# built for localhost works unchanged (the Host header stays localhost:<port>, which the backend's own
# allowed-hosts list already accepts). Reconnects when the link drops. Stop: kill $(cat logs/tunnel.pid).
#   config.env: REMOTE_HOST="stack-host"   (an ~/.ssh/config alias with key + user)
#               TUNNEL_PORTS="3005 8088 9111"
RUN="$1"
. "$RUN/config.env"
mkdir -p "$RUN/logs"
echo $$ > "$RUN/logs/tunnel.pid"
fwd=(); for p in $TUNNEL_PORTS; do fwd+=(-L "$p:localhost:$p"); done
while :; do
  ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      "${fwd[@]}" "$REMOTE_HOST" &
  echo $! > "$RUN/logs/tunnel-ssh.pid"; wait $!
  echo "$(date +%H:%M:%S) tunnel dropped, reconnecting" >> "$RUN/logs/tunnel.log"; sleep 5
done
