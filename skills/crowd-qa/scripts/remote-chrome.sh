#!/usr/bin/env bash
# Runs on REMOTE_HOST, not here. preflight.sh installs it at REMOTE_CHROME (default ~/crowd-qa/remote-chrome.sh).
# usage: remote-chrome.sh start <name> <port> [tz] | stop <name> | list | check
# One headless Chromium per character (or per extra browser), each with its own profile and CDP on
# 127.0.0.1:<port>. The orchestrating machine reaches it through ssh -L (remote-browser.sh). TZ is set only when
# a zone is given (card field "Browser timezone"). Processes are stopped by pid file, never by pattern.
# CHROME_BIN overrides the browser binary; uploads for testers live in CROWD_UPLOADS (default /tmp/crowd-uploads).
D="$HOME/crowd-qa/chrome"; U="${CROWD_UPLOADS:-/tmp/crowd-uploads}"
mkdir -p "$D/run" "$D/profiles" "$U/assets"
bin() {
  for b in "${CHROME_BIN:-}" chromium chromium-browser google-chrome google-chrome-stable; do
    [ -n "$b" ] && command -v "$b" >/dev/null 2>&1 && { command -v "$b"; return 0; }
  done; return 1
}
case "${1:-}" in
  start)
    L="$2"; P="$3"; Z="${4:-}"
    B=$(bin) || { echo "no Chromium on $(hostname): install chromium or set CHROME_BIN" >&2; exit 1; }
    [ -f "$D/run/$L.pid" ] && kill "$(cat "$D/run/$L.pid")" 2>/dev/null && sleep 1
    mkdir -p "$U/$L"
    env ${Z:+TZ=$Z} setsid nohup "$B" --headless=new --remote-debugging-address=127.0.0.1 --remote-debugging-port="$P" \
      --user-data-dir="$D/profiles/$L" --no-first-run --no-default-browser-check --disable-gpu \
      --window-size=1440,900 about:blank > "$D/run/$L.log" 2>&1 < /dev/null &
    echo $! > "$D/run/$L.pid"
    for _ in $(seq 1 30); do
      curl -sf -m1 "http://127.0.0.1:$P/json/version" >/dev/null && { echo "started $L on $P pid $(cat "$D/run/$L.pid")${Z:+ TZ=$Z}"; exit 0; }
      sleep 0.5
    done
    echo "chrome did not answer on $P (see $D/run/$L.log)" >&2; exit 1 ;;
  stop)
    L="$2"; [ -f "$D/run/$L.pid" ] && kill "$(cat "$D/run/$L.pid")" 2>/dev/null; rm -f "$D/run/$L.pid"; echo "stopped $L" ;;
  list)
    for f in "$D"/run/*.pid; do
      [ -f "$f" ] && echo "$(basename "$f" .pid) $(cat "$f") $(kill -0 "$(cat "$f")" 2>/dev/null && echo up || echo dead)"
    done ;;
  check)
    B=$(bin) || { echo "no Chromium on $(hostname): install chromium or set CHROME_BIN" >&2; exit 1; }
    echo "$B, uploads in $U" ;;
  *) echo "usage: remote-chrome.sh start <name> <port> [tz] | stop <name> | list | check" >&2; exit 2 ;;
esac
