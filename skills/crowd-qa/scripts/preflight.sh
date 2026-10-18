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
#   browsers  — remote slots: remote-chrome.sh installed on REMOTE_HOST and a Chromium there; no CDP port of ours
#               already taken here by another session; one spare slot for second-user browsers
#   accounts  — every CODEX_ACCOUNTS / CLAUDE_ACCOUNTS profile exists in caam, quota-guard running, and every Codex
#               account accepts the tester model (a 'go' plan refused the default model; 12 days ran with no evidence)
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
# --- browser slots (references/remote-stack.md) -----------------------------------------------------------
nremote=$(echo "${BROWSER_SLOTS:-}" | tr ' ' '\n' | grep -c '^remote:')
if [ "$nremote" -gt 0 ]; then
  RCP="${REMOTE_CHROME:-~/crowd-qa/remote-chrome.sh}"
  if [ -z "${REMOTE_HOST:-}" ]; then bad "BROWSER_SLOTS has remote slots but REMOTE_HOST is empty"
  elif ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "mkdir -p \$(dirname $RCP) && cat > $RCP && chmod +x $RCP" < "$RUN/scripts/remote-chrome.sh" 2>/dev/null \
       && rc_out=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_HOST" "$RCP check" 2>&1); then
    ok "remote browsers: remote-chrome.sh installed on $REMOTE_HOST ($rc_out)"
  else bad "remote browsers: could not install or run $RCP on $REMOTE_HOST (${rc_out:-ssh failed})"; fi
  for s in $(echo "$BROWSER_SLOTS" | tr ' ' '\n' | grep '^remote:'); do
    p=$(( ${REMOTE_CDP_BASE:-9320} + ${s#remote:} ))
    if python3 -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',$p))==0 else 1)" \
       && ! { [ -f "$RUN/.browsers/port-$p.pid" ] && kill -0 "$(cat "$RUN/.browsers/port-$p.pid")" 2>/dev/null; }; then
      bad "port $p (slot $s) already listens here: another session uses it; change REMOTE_CDP_BASE or stop that session"
    fi
  done
  nweb=$(sed -n 's/^- \*\*Surface\*\*: *//p' "$RUN"/lanes/*/card.md 2>/dev/null | grep -c '^web')
  need=$(( nweb < ${HARD_CAP:-4} ? nweb : ${HARD_CAP:-4} ))
  [ "$nremote" -le "$need" ] && echo "NOTE  $nremote remote browser slot(s) for up to $need web characters at once: add one spare slot, or a tester who needs a second signed-in user waits for a whole day to end"
fi

# --- accounts (quota-guard.sh through caam) ------------------------------------------------------------------
if [ -n "${CODEX_ACCOUNTS:-}${CLAUDE_ACCOUNTS:-}" ]; then
  if command -v "${CAAM:-caam}" >/dev/null; then
    for t in codex claude; do
      acc=$(eval echo "\${$(echo $t | tr a-z A-Z)_ACCOUNTS:-}"); [ -z "$acc" ] && continue
      have=$(${CAAM:-caam} ls "$t" 2>/dev/null | awk '{print ($1=="●" ? $2 : $1)}')
      for a in $acc; do echo "$have" | grep -qx "$a" || bad "caam has no $t profile '$a' (CODEX_ACCOUNTS/CLAUDE_ACCOUNTS)"; done
    done
    pgrep -f "quota-guard.sh $RUN" >/dev/null && ok "quota-guard running (only it switches accounts during the run)" \
      || bad "quota-guard not running: bash $RUN/scripts/restart-watchers.sh $RUN"
  else bad "CODEX_ACCOUNTS/CLAUDE_ACCOUNTS set but caam is not installed"; fi
fi

# --- every Codex account accepts the model (a ChatGPT 'go' plan refused the default model: 12 character-days ran
#     with no evidence). Tested per account without a machine-wide switch when caam has an isolated profile for it.
uses_codex=""
{ [ "${TESTER:-}" = codex ] || [ "${FALLBACK_TESTER:-}" = codex ] || grep -qi '^- \*\*Engine\*\*: *codex' "$RUN"/lanes/*/card.md 2>/dev/null; } && uses_codex=1
if [ -n "$uses_codex" ] && [ -n "${CODEX_ACCOUNTS:-}" ] && command -v "${CAAM:-caam}" >/dev/null; then
  models=$( { [ "${TESTER:-}" = codex ] && echo "${TESTER_MODEL:-default}"
              [ "${FALLBACK_TESTER:-}" = codex ] && echo "${FALLBACK_MODEL:-default}"
              grep -qi '^- \*\*Engine\*\*: *codex' "$RUN"/lanes/*/card.md 2>/dev/null && echo "${CODEX_MODEL:-default}"; } | sort -u)
  isolated=$(${CAAM:-caam} profile ls codex 2>/dev/null | awk 'NF{print ($1=="●" ? $2 : $1)}')
  switch_ok=""
  if [ "${PREFLIGHT_MODEL_PROBE:-}" = switch ]; then
    if pgrep -f "run-tester.sh $RUN " >/dev/null || pgrep -f "crowd.py $RUN" >/dev/null; then
      bad "PREFLIGHT_MODEL_PROBE=switch refused: testers or crowd.py are running and a caam switch would move them"
    else switch_ok=1; orig=$(${CAAM:-caam} ls codex 2>/dev/null | awk '$1=="●"{print $2}'); fi
  fi
  for a in $CODEX_ACCOUNTS; do
    for m in $models; do
      mm=""; [ "$m" != default ] && mm="$m"
      if echo "$isolated" | grep -qx "$a"; then
        out=$(cd /tmp && timeout 180 ${CAAM:-caam} exec codex "$a" -- exec --skip-git-repo-check ${mm:+-m "$mm"} "Reply with the single word ok" 2>&1)
      elif [ -n "$switch_ok" ]; then
        ${CAAM:-caam} activate codex "$a" >/dev/null 2>&1
        out=$(cd /tmp && timeout 180 codex exec --skip-git-repo-check ${mm:+-m "$mm"} "Reply with the single word ok" 2>&1)
      else
        plan=$(${CAAM:-caam} ls codex --json 2>/dev/null | python3 -c "import json,sys
d=json.load(sys.stdin); print(next(((p.get('identity') or {}).get('plan_type','?') for p in d.get('profiles',[]) if p.get('name')=='$a'),'?'))" 2>/dev/null)
        case "$plan" in
          go|free) bad "codex account $a is on a ChatGPT '$plan' plan, which refused the default model in a real run: prove model $m with PREFLIGHT_MODEL_PROBE=switch (switches accounts while nothing runs) or an isolated profile (caam profile add codex $a), pin a model it accepts, or drop it from CODEX_ACCOUNTS" ;;
          *) echo "NOTE  codex account $a ($plan plan): model $m not tested (no isolated caam profile; PREFLIGHT_MODEL_PROBE=switch tests it). Pin TESTER_MODEL/CODEX_MODEL to a model every account accepts" ;;
        esac
        continue
      fi
      if echo "$out" | tail -5 | grep -qiw ok; then ok "codex account $a accepts model $m"
      else bad "codex account $a refuses model $m: $(echo "$out" | grep -iE 'error|not supported|model' | tail -1 | cut -c1-160)"; fi
    done
  done
  [ -n "$switch_ok" ] && [ -n "$orig" ] && ${CAAM:-caam} activate codex "$orig" >/dev/null 2>&1
fi

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

if [ "${FIXING:-off}" = on ]; then
  case "${RUNNER:-auto}" in t3|subagent|cli|auto) ok "fix runner: ${RUNNER:-auto}" ;; *) bad "RUNNER=${RUNNER} is not t3, subagent, cli or auto" ;; esac
  [ "${RUNNER:-auto}" = t3 ] || for t in ${TRACKERS:-}; do
    k="${t%%=*}"; p=$(for r in ${REPOS:-}; do [ "${r%%=*}" = "$k" ] && echo "${r#*=}"; done)
    if [ -n "$p" ] && git -C "$p" rev-parse --git-dir >/dev/null 2>&1; then ok "fix: local clone for $k at $p"
    else bad "fix: no local clone for tracker $k in REPOS (\"$k=/path/to/clone\"); cli and subagent runners make worktrees from it"; fi
  done
  kinds=" ${TRACKER_KIND:-} "
  if [ -z "${FE_DEV_CMD:-}" ] && ! echo "$kinds" | grep -q "=be "; then
    bad "fix: FE_DEV_CMD is empty: frontend fixes cannot be run for before/after evidence or re-checks"
  fi
  echo "$kinds" | grep -q "=be " && { [ -n "${BE_DEPLOY_CMD:-}" ] && ok "fix: BE_DEPLOY_CMD set" || bad "fix: a backend tracker but BE_DEPLOY_CMD is empty"; }
  [ -n "${CHECK_CMD:-}" ] || set | grep -q '^CHECK_CMD_' || echo "NOTE  fix: no CHECK_CMD; fixers will look for the repo's own checks"
  for f in fix-brief review-brief recheck-brief; do [ -f "$RUN/templates/$f.md" ] || bad "fix: templates/$f.md missing"; done
  if [ -n "${RECHECK_COMMENT_HOST:-}" ]; then
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$RECHECK_COMMENT_HOST" 'gh auth status' >/dev/null 2>&1 \
      && ok "fix: re-check comments go out from gh on $RECHECK_COMMENT_HOST" || bad "fix: gh on $RECHECK_COMMENT_HOST is not logged in"
  fi
fi

exit $fail
