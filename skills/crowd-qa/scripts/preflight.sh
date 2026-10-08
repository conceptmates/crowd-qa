#!/usr/bin/env bash
# usage: preflight.sh <run-dir>
# Run before every launch and every relaunch. Prints PASS/FAIL per check and exits 1 on any FAIL.
# Each check is a failure that cost a real crowd run hours (references/lessons.md):
#   disk      — a full disk took down Postgres and Docker and 17 of 21 characters never reached the app
#   docker    — Docker can hang while `docker ps` stays silent; a local stack is then dead
#   tunnel    — remote mode: every port in TUNNEL_PORTS must answer through the tunnel
#   stack     — STACK_VERIFY runs the app's real calls end to end (sign-up, the core create flow, uploads);
#               env flags that read "1" as false and missing keys only show up here
#   devices   — mobile only: agent-device installed globally (npx costs 1.4 s per call), devices present,
#               the iOS device runner prepared once per simulator
#   engines   — the user chose the tester engines (ENGINE_CONFIRMED=yes, references/engines.md); every CLI they
#               name answers, and so does LLM_CLI, which crowd.py uses to plan, judge, verify and report
#   watchers  — api-watch and the state saver are running, so an outage holds the crowd and state is on disk
#   leftovers — runners still alive from an earlier crowd.py are reported (crowd.py waits for them)
set -u
RUN="$1"; . "$RUN/config.env"
fail=0
ok()  { printf 'PASS  %s\n' "$*"; }
bad() { printf 'FAIL  %s\n' "$*"; fail=1; }

free_gb=$(df -Pk "$RUN" | awk 'NR==2{print int($4/1048576)}')
[ "$free_gb" -ge "${DISK_MIN_GB:-15}" ] && ok "disk: ${free_gb} GB free" || bad "disk: only ${free_gb} GB free (need ${DISK_MIN_GB:-15}); clear space before launching"

if [ "${STACK_MODE:-local}" = local ]; then
  if command -v docker >/dev/null && [ -n "$(docker ps -q 2>/dev/null | head -1)" ]; then
    timeout 20 docker ps >/dev/null 2>&1 && ok "docker answers" || bad "docker does not answer in 20 s (restart Docker)"
  fi
else
  pgrep -f "tunnel.sh $RUN" >/dev/null || pgrep -f "$RUN/stack/tunnel.sh" >/dev/null && ok "tunnel process running" || bad "tunnel not running: nohup bash $RUN/scripts/tunnel.sh $RUN &"
  for p in $TUNNEL_PORTS; do
    c=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "http://localhost:$p/" 2>/dev/null)
    [ "$c" != "000" ] && ok "port $p answers through the tunnel ($c)" || bad "port $p: no answer through the tunnel"
  done
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" 'df -h / | tail -1' >/dev/null 2>&1 && ok "remote shell reachable ($REMOTE_HOST)" || bad "ssh $REMOTE_HOST fails"
fi

if [ -n "${STACK_VERIFY:-}" ]; then
  if bash -c "$STACK_VERIFY" > "$RUN/logs/stack-verify.log" 2>&1; then ok "stack check: $(tail -1 "$RUN/logs/stack-verify.log")"
  else bad "stack check failed (see logs/stack-verify.log):"; grep -E '^FAIL' "$RUN/logs/stack-verify.log" | sed 's/^/      /'; fi
else bad "STACK_VERIFY is not set: write a stack check for this app (templates/stack-verify.example.py)"; fi

surfaces=$( { echo "${TARGET:-web}"; sed -n 's/^- \*\*Surface\*\*: *//p' "$RUN"/lanes/*/card.md 2>/dev/null; } | sort -u)
case "$surfaces" in *web*) command -v agent-browser >/dev/null && ok "agent-browser installed" || bad "agent-browser not on PATH (web characters need it)";; esac
case "$surfaces" in *ios*|*android*)
  command -v agent-device >/dev/null && ok "agent-device installed globally" || bad "agent-device not on PATH: npm i -g agent-device"
  [ -n "${APP_PATH:-}" ] && { [ -e "$APP_PATH" ] && ok "app build present: $APP_PATH" || bad "APP_PATH missing: $APP_PATH"; } ;;
esac
case "$surfaces" in *ios*)
  n=0; for u in $DEVICE_POOL; do xcrun simctl list devices 2>/dev/null | grep -q "$u" && n=$((n+1)); done
  [ "$n" -gt 0 ] && ok "$n simulator(s) from DEVICE_POOL present" || bad "no DEVICE_POOL simulator found"
  ls ~/.agent-device/apple-runner/derived/ios-simulator/*/Build/Products/*.xctestrun >/dev/null 2>&1 && ok "iOS device runner built" \
    || bad "device runner not prepared: agent-device prepare ios-runner --platform ios --udid <udid>" ;;
esac
case "$surfaces" in *android*) command -v adb >/dev/null && [ -n "$(adb devices | awk 'NR>1 && $2=="device"')" ] && ok "android device online" || bad "no android emulator online";; esac
case "$surfaces" in *cli*) [ -n "${CLI_CMD:-}" ] && ok "CLI_CMD set" || bad "CLI_CMD empty (cli characters need it)";; esac
case "$surfaces" in *api*) [ -n "${API_URL:-}" ] && curl -s -o /dev/null -m 5 "$API_URL" && ok "API_URL answers" || bad "API_URL empty or not answering";; esac

if [ "${ENGINE_CONFIRMED:-}" = yes ]; then
  ok "engines chosen: TESTER=${TESTER:-?} ${TESTER_MODEL:-} ${TESTER_EFFORT:-}${FALLBACK_TESTER:+, fallback $FALLBACK_TESTER}"
else
  bad "tester engines not chosen yet. Ask the user before launch; references/engines.md has the question and the"
  sed -n '/^## The question/,/^## /p' "$(dirname "$0")/../references/engines.md" 2>/dev/null | sed '$d' | sed 's/^/      /'
  echo "      Then set TESTER, TESTER_MODEL, TESTER_EFFORT (and CODEX_MODEL/CODEX_EFFORT, any '- **Engine**:' card lines) and ENGINE_CONFIRMED=yes in config.env."
fi
engines=$( { echo "${TESTER:-claude}" "${FALLBACK_TESTER:-}" "${LLM_CLI:-claude}"; sed -n 's/^- \*\*Engine\*\*: *//p' "$RUN"/lanes/*/card.md 2>/dev/null; } | tr ' ' '\n' | awk 'NF{print tolower($1)}' | sort -u)
for t in $engines; do
  command -v "$t" >/dev/null && ok "engine CLI: $t $($t --version 2>&1 | head -1)" || bad "engine CLI $t missing"
done
pgrep -f "state-saver.sh $RUN" >/dev/null && ok "state saver running" || bad "state saver not running: bash $RUN/scripts/restart-watchers.sh $RUN"
if [ -n "${HEALTH_CHECKS:-}" ]; then
  pgrep -f "api-watch.sh $RUN" >/dev/null && ok "api-watch running" || bad "api-watch not running: bash $RUN/scripts/restart-watchers.sh $RUN"
  # run every check now: a missing canary account (a reset database) passes the stack check but holds the crowd
  hc=$(bash "$RUN/scripts/api-watch.sh" "$RUN" once 2>&1) && ok "health checks pass now" || bad "health checks fail now ($hc): fix them, or the crowd waits on STACK_DOWN"
  [ -n "${NOTIFY_CMD:-}" ] && ok "NOTIFY_CMD set" || echo "NOTE  NOTIFY_CMD is empty: nobody hears about an outage until someone runs status (a dry run sat 90 minutes on a missing canary)"
  [ -f "$RUN/STACK_DOWN" ] && bad "STACK_DOWN is set: $(cat "$RUN/STACK_DOWN")"
else bad "HEALTH_CHECKS is empty: without api-watch an outage burns character-days (references/hooks.md)"; fi

left=$(pgrep -f "run-tester.sh $RUN " | wc -l | tr -d ' ')
[ "$left" = 0 ] && ok "no runners alive" || ok "$left runner(s) still alive from an earlier start: crowd.py waits for them instead of starting a second copy"
[ -f "$RUN/lanes.json" ] && ok "lanes.json: $(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$RUN/lanes.json") characters" || bad "lanes.json missing: [{\"id\", \"role\", \"surface\", \"depends_on\": []}, ...]"

exit $fail
