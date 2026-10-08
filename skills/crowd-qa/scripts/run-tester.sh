#!/usr/bin/env bash
# usage: run-tester.sh <run-dir> <character> <day>
# Runs one character for one simulated day, blocking, and writes lanes/<id>/round<day>/DONE when finished.
# The prompt is built from the brief template, the character card, their diary (memory.md), context.md,
# the world registry, the town square digest, the day's scenarios and the judge's feedback on the day before.
#
# Surfaces (card field "Surface", default TARGET in config.env):
#   web      headless browser through agent-browser, one session per character
#   ios      a simulator from DEVICE_POOL through agent-device
#   android  an emulator from DEVICE_POOL through agent-device
#   cli      the product's command-line tool, run in round<day>/work/
#   api      the product's HTTP API (curl), requests and responses logged in round<day>/evidence/
#
# Engines (config.env): TESTER = codex | claude | opencode, chosen in preflight (references/engines.md). A card
# field "Engine" gives one character its own engine; that engine's model and effort come from CODEX_MODEL /
# CLAUDE_MODEL / OPENCODE_MODEL and the matching *_EFFORT (TESTER_MODEL / TESTER_EFFORT when it is TESTER).
# Claude testers start lean (CLAUDE_LEAN=1: no user settings, hooks, plugins, MCP servers or skills, about half
# the fixed context) and run in chunks of CHUNK_USD dollars: when a chunk is spent, a fresh session continues from
# report.json, so the context never grows to hundreds of thousands of tokens re-read on every turn.
# When an engine hits its usage limit and FALLBACK_TESTER is set, the runner writes TESTER_FALLBACK (naming the
# engine that ran out) and that engine's characters continue on the fallback, from report.json. Without a
# fallback, or when the fallback runs out too, it writes QUOTA_PAUSE.<engine> and stops the day: only characters
# on that engine wait; crowd.py lifts the pause after RETRY_PRIMARY_MIN minutes and starts the day again.
set -u
if [ -z "${RUNNER_FROZEN:-}" ]; then
  # freeze a copy per character-day so editing the script mid-run never changes a running day
  C="$1/.runner/$2-$3"; mkdir -p "$C"; cp "$0" "$C/run-tester.sh"
  RUNNER_FROZEN=1 exec bash "$C/run-tester.sh" "$@"
fi
RUN="$1"; LANE="$2"; ROUND="$3"
. "$RUN/config.env"
LD="$RUN/lanes/$LANE"; OUT="$LD/round$ROUND"
mkdir -p "$OUT/shots" "$OUT/evidence"; rm -f "$OUT/DONE"
SESSION="crowd-$LANE"
card_field() { sed -n "s/^- \*\*$1\*\*: *//p" "$LD/card.md" | head -1; }
NAME=$(card_field Name); SURFACE=$(card_field Surface); SURFACE=${SURFACE:-$TARGET}
EXCLUSIVE=$(card_field Exclusive); CARD_ENGINE=$(card_field Engine | awk '{print tolower($1)}')
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
free_gb() { df -Pk "$RUN" | awk 'NR==2{print int($4/1048576)}'; }

state() {
  python3 - "$LD/state.json" "$LANE" "$ROUND" "$1" "${2:-}" <<'EOF'
import json, os, sys, time
p, lane, rnd, status, extra = sys.argv[1:6]
d = json.load(open(p)) if os.path.exists(p) else {"lane": lane}
d.update({"round": int(rnd), "status": status, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")})
if extra:
    d.update(json.loads(extra))
json.dump(d, open(p, "w"), indent=1)
EOF
}
cleanup() {
  [ "${EXCL_OWNER:-}" = 1 ] && bash "$RUN/scripts/net-stall.sh" "$RUN" resume >/dev/null 2>&1
  case "$SURFACE" in ios|android) bash "$RUN/scripts/device-pool.sh" "$RUN" release "$LANE" ;; esac
  [ "${EXCL_OWNER:-}" = 1 ] && rm -f "$RUN/.exclusive"
  rm -f "$RUN/.active/$LANE"
}
done_with() {
  echo "$1" > "$OUT/DONE"; state "done" "{\"done\": \"$1\"}"
  # every finished day lands on disk, whatever happens to the orchestrating session
  python3 "$RUN/scripts/save-state.py" "$RUN" ledger "$LANE" "$ROUND" "$1" >/dev/null 2>&1
  cleanup; exit 0
}
trap cleanup EXIT


# An exclusive character (one who stalls the shared API to fake a weak network) runs alone: everyone else
# waits while it holds .exclusive, and it waits for the others to finish first. This costs the whole crowd
# its parallelism, so prefer faking a weak network inside the character's own browser (agent-browser offline).
held_by_other() { [ -f "$RUN/.exclusive" ] && [ "$(awk '{print $1}' "$RUN/.exclusive")" != "$LANE" ] && kill -0 "$(awk '{print $2}' "$RUN/.exclusive")" 2>/dev/null; }
state "waiting-capacity"
while held_by_other; do sleep 30; done
if [ "$EXCLUSIVE" = yes ]; then
  until mkdir "$RUN/.exclusive.lock" 2>/dev/null; do sleep 30; done
  while held_by_other; do sleep 30; done
  echo "$LANE $$" > "$RUN/.exclusive"; EXCL_OWNER=1; rmdir "$RUN/.exclusive.lock"
  while [ "$(find "$RUN/.active" -type f ! -name "$LANE" | wc -l | tr -d ' ')" -gt 0 ]; do sleep 30; done
fi
# a full disk has killed databases mid-run before: never start below DISK_MIN_GB
while [ "$(free_gb)" -lt "${DISK_MIN_GB:-15}" ]; do
  state "waiting-disk"; echo "$(date +%H:%M:%S) [$LANE] WAIT disk under ${DISK_MIN_GB:-15} GB free" >> "$RUN/capacity.log"; sleep 120
done
bash "$RUN/scripts/capacity.sh" "$RUN" wait "$LANE" $$

DEVICE=""
case "$SURFACE" in ios|android)
  state "waiting-device"
  DEVICE=$(bash "$RUN/scripts/device-pool.sh" "$RUN" acquire "$LANE" "$ROUND" $$ | tail -1)
  [ -z "$DEVICE" ] && done_with "exit=1 rc=no-device" ;;
esac
# never start on a broken stack: api-watch.sh writes STACK_DOWN and the watchdog repairs it. The runner also
# probes the stack itself, so a dead server holds the crowd even when the watcher is not running.
stack_ok() { [ ! -f "$RUN/STACK_DOWN" ] && { [ -z "${HEALTH_CHECKS:-}" ] || bash "$RUN/scripts/api-watch.sh" "$RUN" once >/dev/null 2>&1; }; }
until stack_ok; do
  state "waiting-stack"; echo "$(date +%H:%M:%S) [$LANE] WAIT stack: $(bash "$RUN/scripts/api-watch.sh" "$RUN" once 2>&1 | tail -1)" >> "$RUN/capacity.log"; sleep "${STACK_POLL_S:-60}"
done
mkdir -p "$RUN/.active"; touch "$RUN/.active/$LANE"
state "running" "{\"device\": \"$DEVICE\", \"surface\": \"$SURFACE\"}"

case "$SURFACE" in
  web) TOOL_NOTE="Your browser is the agent-browser session $SESSION (headless). Never run agent-browser close --all, never start another session for yourself. Need a second user signed in at the same time? Use the session $SESSION-2." ;;
  ios|android) TOOL_NOTE="Your device is $DEVICE: pass --udid $DEVICE (or --serial) to agent-device. Prefer scripts/act.py for taps and fills." ;;
  cli) mkdir -p "$OUT/work"; TOOL_NOTE="You use the product's command-line tool (see Run context for how to run it). Work in $OUT/work. Save each flow's terminal output to $OUT/evidence/<scenario-id>.txt (command, output, exit code); those files are your screenshots." ;;
  api) TOOL_NOTE="You use the product's HTTP API (see Run context for the base URL and how to authenticate). Use curl -sS -i and save every request and response pair to $OUT/evidence/<scenario-id>-<step>.http; those files are your screenshots." ;;
  *) TOOL_NOTE="" ;;
esac

# The values testers use in commands are exported, so the brief stays identical for every character and the
# shared part of the prompt (brief, then context) is a real cache prefix across the crowd.
export RUN LANE ROUND OUT NAME SURFACE SESSION DEVICE APP_URL="${APP_URL:-}" APP_ID="${APP_ID:-}"
PREV=$((ROUND-1))
day_section() {  # only today's scenarios plus the sweep; the other days' plans are dead weight in the prompt
  awk -v d="$ROUND" '
    /^## / { keep = ($0 ~ "^## Day " d "([^0-9]|$)") || tolower($0) ~ /sweep/ }
    keep' "$LD/scenarios.md"
}
{
  cat "$RUN/templates/tester-brief-character.md"
  [ -f "$RUN/context.md" ] && { echo; cat "$RUN/context.md"; }
  # everything below differs per character
  echo; echo "## Your assignment"
  echo "You are $NAME (character id $LANE), day $ROUND, surface $SURFACE. These are set in your shell environment:"
  echo "RUN=$RUN  LANE=$LANE  ROUND=$ROUND  OUT=$OUT  SESSION=$SESSION${DEVICE:+  DEVICE=$DEVICE}${APP_URL:+  APP_URL=$APP_URL}${APP_ID:+  APP_ID=$APP_ID}"
  echo "File tools need absolute paths: use the values above, not the variable names."
  echo; echo "## Who you are"; cat "$LD/card.md"
  echo; echo "## Your memory (what you did and felt on earlier days)"
  if [ -s "$LD/memory.md" ]; then cat "$LD/memory.md"; else echo "(day 1: you have never used this product)"; fi
  if [ -s "$RUN/stack-faults.md" ]; then
    echo; echo "## Known test-setup problems (not product bugs: work around them, never report them)"; tail -40 "$RUN/stack-faults.md"
  fi
  echo; echo "## The world right now (what other characters have made)"; python3 "$RUN/scripts/world.py" "$RUN" list
  echo; echo "## The town square (newest ${SQUARE_DIGEST_LIMIT:-20}; older: python3 \$RUN/scripts/square.py \$RUN digest \$LANE --limit 80)"
  python3 "$RUN/scripts/square.py" "$RUN" digest "$LANE" --limit "${SQUARE_DIGEST_LIMIT:-20}"
  echo; echo "## Day $ROUND scenarios"
  if grep -q "^## Day $ROUND" "$LD/scenarios.md"; then day_section; else cat "$LD/scenarios.md"; fi
  if [ -f "$LD/feedback-r$PREV.md" ]; then
    echo; echo "## Reviewer feedback on day $PREV (address all of it)"; cat "$LD/feedback-r$PREV.md"
  fi
  if [ -f "$LD/round$PREV/verdict.json" ]; then
    echo; echo "## Your bugs from day $PREV (already recorded: re-check, never report again)"
    echo "Re-test each one once, on the way to today's scenarios. Record the result under \"rechecks\" in report.json"
    echo "({\"title\", \"status\": \"still|fixed|changed\", \"evidence\"}). Put one in findings only if it now fails in a new way."
    python3 -c "import json,sys;[print('-',f.get('title'),'|',f.get('route','')) for f in json.load(open(sys.argv[1])).get('verified_findings',[])]" "$LD/round$PREV/verdict.json" 2>/dev/null
  fi
  [ -s "$RUN/filed.txt" ] && { echo; echo "## Already filed (do not re-report; a me-too on the square is fine)"; cat "$RUN/filed.txt"; }
  echo; echo "## Your tools"; echo "$TOOL_NOTE"
  echo; echo "## If the test servers go down"
  echo "Before each scenario, and whenever the product fails in a way that looks like the server (5xx, sign-in refused with the right password, blank page): run  test -f \$RUN/STACK_DOWN && cat \$RUN/STACK_DOWN . If it exists the servers are down: record nothing, wait with  while [ -f \$RUN/STACK_DOWN ]; do sleep 60; done , then sign in again and redo the step. Never report an error you saw while STACK_DOWN existed."
} > "$OUT/prompt.md"

cd "$OUT"
export AGENT_DEVICE_SESSION="$SESSION" AGENT_BROWSER_SESSION="$SESSION"
MSG="You were interrupted (an engine or account switch, a quota pause, or a restart). You are still $NAME. Continue where you stopped: keep updating $OUT/report.json and your memory, finish every not_run scenario and the sweep, then give the 2-line summary."
# resuming a session that another engine continued after it: the session's memory is stale, the files are not
MSG_HANDOFF="You were interrupted and another tester continued your day in the meantime. You are still $NAME. $OUT/report.json is the current truth (it may have moved past where you remember): read it first, keep every result and evidence path in it, then continue with each not_run scenario and the sweep, keep updating report.json and your memory, and give the 2-line summary."
limit_hit() { tail -c 4000 "$1" 2>/dev/null | grep -qiE "hit your usage limit|usage limit reached|hit your limit|rate limit exceeded|out of extra usage|quota exceeded"; }
# markers older than RETRY_PRIMARY_MIN expire, so the primary engine gets another try after a limit
expire_marker() { [ -f "$1" ] && [ $(( $(date +%s) - $(mtime "$1") )) -ge $(( ${RETRY_PRIMARY_MIN:-60} * 60 )) ] && rm -f "$1"; }

# run_engine <engine> <model> <effort> <log>: one attempt, fresh or resumed
run_engine() {
  local E="$1" M="$2" F="$3" LOG="$4" SID
  case "$E" in
    codex)
      # the newest codex session of this day, even if another engine ran in between: resume beats a fresh start
      # (only when a log exists: grep with no file argument would read stdin and block the runner)
      SID=""; LOGS=$(ls -t "$OUT"/tester*.log 2>/dev/null)
      [ -n "$LOGS" ] && SID=$(grep -h -m1 '^session id:' $LOGS 2>/dev/null < /dev/null | head -1 | awk '{print $3}')
      if [ -n "$RESUME" ] && [ -n "$SID" ]; then
        [ "$PREV_ENGINE" = codex ] && R_MSG="$MSG" || R_MSG="$MSG_HANDOFF"
        timeout "$ROUND_TIMEOUT_S" codex exec resume "$SID" --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
          ${M:+-m "$M"} ${F:+-c model_reasoning_effort="$F"} "$R_MSG" > "$LOG" 2>&1
      else
        timeout "$ROUND_TIMEOUT_S" codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
          ${M:+-m "$M"} ${F:+-c model_reasoning_effort="$F"} < "$OUT/prompt.md" > "$LOG" 2>&1
      fi ;;
    claude)
      local LEAN=""; [ "${CLAUDE_LEAN:-1}" = 1 ] && LEAN="--setting-sources project --strict-mcp-config --disable-slash-commands"
      local BUDGET=""; [ -n "${CHUNK_USD:-5}" ] && [ "${CHUNK_USD:-5}" != 0 ] && BUDGET="--max-budget-usd ${CHUNK_USD:-5}"
      SID=$(cat "$OUT/claude-session" 2>/dev/null)
      if [ -n "$RESUME" ] && [ -n "$SID" ] && [ -z "$FRESH" ]; then
        [ "$PREV_ENGINE" = claude ] && R_MSG="$MSG" || R_MSG="$MSG_HANDOFF"
        timeout "$ROUND_TIMEOUT_S" claude -p --resume "$SID" ${M:+--model "$M"} ${F:+--effort "$F"} $LEAN $BUDGET \
          --dangerously-skip-permissions "$R_MSG" < /dev/null > "$LOG" 2>&1
      else
        SID=$(uuidgen | tr 'A-Z' 'a-z'); echo "$SID" > "$OUT/claude-session"
        timeout "$ROUND_TIMEOUT_S" claude -p --session-id "$SID" ${M:+--model "$M"} ${F:+--effort "$F"} $LEAN $BUDGET \
          --dangerously-skip-permissions < "$OUT/prompt.md" > "$LOG" 2>&1
      fi ;;
    opencode)
      if [ -n "$RESUME" ] && [ -s "$OUT/opencode-session" ]; then
        [ "$PREV_ENGINE" = opencode ] && R_MSG="$MSG" || R_MSG="$MSG_HANDOFF"
        timeout "$ROUND_TIMEOUT_S" opencode run --auto -s "$(cat "$OUT/opencode-session")" ${M:+-m "$M"} "$R_MSG" > "$LOG" 2>&1
      else
        timeout "$ROUND_TIMEOUT_S" opencode run --auto --title "crowd-$LANE-d$ROUND" ${M:+-m "$M"} -f "$OUT/prompt.md" \
          "Read the attached file: it is your full brief. Follow it exactly." > "$LOG" 2>&1
        opencode session list 2>/dev/null | grep -m1 "crowd-$LANE-d$ROUND" | awk '{print $1}' > "$OUT/opencode-session"
      fi ;;
    *) echo "unknown engine $E" > "$LOG"; return 2 ;;
  esac
}

has_session() {
  case "$1" in
    codex) grep -qh '^session id:' "$OUT"/tester*.log 2>/dev/null ;;
    claude) [ -s "$OUT/claude-session" ] ;;
    opencode) [ -s "$OUT/opencode-session" ] ;;
    *) return 1 ;;
  esac
}
# model and effort for an engine: TESTER's own settings, the fallback's, else <ENGINE>_MODEL / <ENGINE>_EFFORT
engine_model() {
  local U; U=$(echo "$1" | tr 'a-z' 'A-Z')
  if [ "$1" = "${TESTER:-codex}" ]; then echo "${TESTER_MODEL:-}|${TESTER_EFFORT:-}"
  elif [ "$1" = "${FALLBACK_TESTER:-}" ]; then echo "${FALLBACK_MODEL:-}|${FALLBACK_EFFORT:-}"
  else eval "echo \"\${${U}_MODEL:-}|\${${U}_EFFORT:-}\""; fi
}
TRIES=0; CHUNKS=0; FRESH=""
while :; do
  TRIES=$((TRIES+1)); [ "$TRIES" -gt 30 ] && done_with "exit=1 rc=too-many-switches"
  . "$RUN/config.env"   # re-read each attempt, so model and effort changes apply on the next switch
  expire_marker "$RUN/TESTER_FALLBACK"
  ENGINE="${CARD_ENGINE:-${TESTER:-codex}}"
  # the fallback only replaces an engine that ran out (the marker names it), not every character's engine
  if [ -f "$RUN/TESTER_FALLBACK" ] && [ -n "${FALLBACK_TESTER:-}" ] && grep -q "from=$ENGINE " "$RUN/TESTER_FALLBACK"; then
    ENGINE="$FALLBACK_TESTER"
  fi
  expire_marker "$RUN/QUOTA_PAUSE.$ENGINE"
  [ -f "$RUN/QUOTA_PAUSE.$ENGINE" ] && done_with "exit=quota rc=$ENGINE-paused engine=$ENGINE"
  MODEL="$(engine_model "$ENGINE" | cut -d'|' -f1)"; EFFORT="$(engine_model "$ENGINE" | cut -d'|' -f2)"
  PREV_ENGINE=$(cat "$OUT/engine" 2>/dev/null); echo "$ENGINE" > "$OUT/engine"
  RESUME=""; [ -s "$OUT/report.json" ] && { cp "$OUT/report.json" "$OUT/report.partial.json"; RESUME=1; }
  N=$(ls "$OUT"/tester*.log 2>/dev/null | wc -l | tr -d ' ')
  LOG="$OUT/tester.log"; [ "$N" -gt 0 ] && LOG="$OUT/tester.run$N.log"
  state "running" "{\"engine\": \"$ENGINE\", \"device\": \"$DEVICE\", \"surface\": \"$SURFACE\"}"
  echo "$(date) attempt $TRIES engine=$ENGINE prev=${PREV_ENGINE:-none} resume=${RESUME:-no}" >> "$OUT/resume.log"
  # a fresh session (no earlier session for this engine) needs the handoff note in its prompt
  if [ -n "$RESUME" ] && { [ -n "$FRESH" ] || { [ "$PREV_ENGINE" != "$ENGINE" ] && ! has_session "$ENGINE"; }; }; then
    printf '\n## RESUME (%s)\nThis day was started by another tester and interrupted. Partial progress: %s/report.partial.json, evidence in %s/shots/ and %s/evidence/, your memory in memory.md. Copy the partial report to report.json, keep its valid results and evidence, continue with every not_run scenario.\n' "$(date +%H:%M)" "$OUT" "$OUT" "$OUT" >> "$OUT/prompt.md"
  fi
  run_engine "$ENGINE" "$MODEL" "$EFFORT" "$LOG"; RC=$?; FRESH=""
  # a spent chunk is not a failure: a fresh session carries on from report.json with a short context
  if [ "$ENGINE" = claude ] && tail -c 2000 "$LOG" | grep -q "Exceeded USD budget"; then
    CHUNKS=$((CHUNKS+1))
    if [ "$CHUNKS" -lt "${MAX_CHUNKS:-8}" ]; then
      echo "$(date) chunk $CHUNKS spent (CHUNK_USD=${CHUNK_USD:-5}): fresh session continues" >> "$OUT/resume.log"
      FRESH=1; continue
    fi
    RC=0; break
  fi
  if limit_hit "$LOG"; then
    if [ ! -f "$RUN/TESTER_FALLBACK" ] && [ -n "${FALLBACK_TESTER:-}" ] && [ "$ENGINE" != "$FALLBACK_TESTER" ]; then
      # the primary ran out: every character's next attempt uses the fallback (this one right away)
      echo "since=$(date +%s) from=$ENGINE to=$FALLBACK_TESTER lane=$LANE" > "$RUN/TESTER_FALLBACK"
      continue
    fi
    # no fallback, or the fallback is out too: this engine pauses; crowd.py starts the day again later
    [ -f "$RUN/QUOTA_PAUSE.$ENGINE" ] || echo "engine=$ENGINE lane=$LANE at $(date)" > "$RUN/QUOTA_PAUSE.$ENGINE"
    done_with "exit=quota rc=$ENGINE-limit engine=$ENGINE"
  fi
  break
done
case "$SURFACE" in
  web) agent-browser close >/dev/null 2>&1; AGENT_BROWSER_SESSION="$SESSION-2" agent-browser close >/dev/null 2>&1 ;;
  ios|android) agent-device close >/dev/null 2>&1 ;;
esac
done_with "exit=$RC engine=$ENGINE"
