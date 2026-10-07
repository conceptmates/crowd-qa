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
# Engines (config.env): TESTER = codex | claude | opencode, chosen in preflight. When a tester hits its usage
# limit and FALLBACK_TESTER is set, the runner writes TESTER_FALLBACK and every new attempt uses the fallback;
# the primary is tried again RETRY_PRIMARY_MIN minutes later. When the fallback runs out too, the runner writes
# QUOTA_PAUSE and stops the day; the workflow waits RETRY_PRIMARY_MIN and resumes it. A switch resumes the same
# session when it can, otherwise starts a fresh one carrying report.partial.json.
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
EXCLUSIVE=$(card_field Exclusive)
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

# QUOTA_PAUSE exists only when every engine is out (all tester accounts AND the fallback)
[ -f "$RUN/QUOTA_PAUSE" ] && done_with "exit=quota rc=paused-before-start"

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
# never start on a broken stack: api-watch.sh writes STACK_DOWN and the watchdog repairs it
while [ -f "$RUN/STACK_DOWN" ]; do state "waiting-stack"; sleep 30; done
mkdir -p "$RUN/.active"; touch "$RUN/.active/$LANE"
state "running" "{\"device\": \"$DEVICE\", \"surface\": \"$SURFACE\"}"

case "$SURFACE" in
  web) TOOL_NOTE="Your browser is the agent-browser session $SESSION (headless). Never run agent-browser close --all, never start another session for yourself. Need a second user signed in at the same time? Use the session $SESSION-2." ;;
  ios|android) TOOL_NOTE="Your device is $DEVICE: pass --udid $DEVICE (or --serial) to agent-device. Prefer scripts/act.py for taps and fills." ;;
  cli) mkdir -p "$OUT/work"; TOOL_NOTE="You use the product's command-line tool (see Run context for how to run it). Work in $OUT/work. Save each flow's terminal output to $OUT/evidence/<scenario-id>.txt (command, output, exit code); those files are your screenshots." ;;
  api) TOOL_NOTE="You use the product's HTTP API (see Run context for the base URL and how to authenticate). Use curl -sS -i and save every request and response pair to $OUT/evidence/<scenario-id>-<step>.http; those files are your screenshots." ;;
  *) TOOL_NOTE="" ;;
esac

{
  sed -e "s|\$SESSION|$SESSION|g" -e "s|\$OUT|$OUT|g" -e "s|\$RUN|$RUN|g" -e "s|\$LANE|$LANE|g" \
      -e "s|\$NAME|$NAME|g" -e "s|\$SURFACE|$SURFACE|g" -e "s|\$DEVICE|$DEVICE|g" -e "s|\$APP_ID|${APP_ID:-}|g" \
      -e "s|\$APP_URL|${APP_URL:-}|g" -e "s|\$ROUND|$ROUND|g" "$RUN/templates/tester-brief-character.md"
  echo; echo "## Who you are"; cat "$LD/card.md"
  echo; echo "## Your memory (what you did and felt on earlier days)"
  if [ -s "$LD/memory.md" ]; then cat "$LD/memory.md"; else echo "(day 1: you have never used this product)"; fi
  [ -f "$RUN/context.md" ] && { echo; cat "$RUN/context.md"; }
  echo; echo "## The world right now (what other characters have made)"; python3 "$RUN/scripts/world.py" "$RUN" list
  echo; echo "## The town square (newest first)"; python3 "$RUN/scripts/square.py" "$RUN" digest "$LANE"
  echo; echo "## Day $ROUND scenarios"; cat "$LD/scenarios.md"
  PREV=$((ROUND-1))
  if [ -f "$LD/feedback-r$PREV.md" ]; then
    echo; echo "## Reviewer feedback on day $PREV (address all of it)"; cat "$LD/feedback-r$PREV.md"
    [ -f "$LD/round$PREV/report.json" ] && echo "Previous report: $LD/round$PREV/report.json (carry forward still-valid results with their evidence paths, re-verify, extend)."
  fi
  [ -f "$RUN/filed.txt" ] && { echo; echo "## Already filed (do not re-report; a me-too on the square is fine)"; cat "$RUN/filed.txt"; }
  echo; echo "## Your tools"; echo "$TOOL_NOTE"
  echo; echo "## If the test servers go down"
  echo "Before each scenario, and whenever the product fails in a way that looks like the server (5xx, sign-in refused with the right password, blank page): run  test -f $RUN/STACK_DOWN && cat $RUN/STACK_DOWN . If it exists the servers are down: record nothing, wait with  while [ -f $RUN/STACK_DOWN ]; do sleep 60; done , then sign in again and redo the step. Never report an error you saw while STACK_DOWN existed."
} > "$OUT/prompt.md"

cd "$OUT"
export AGENT_DEVICE_SESSION="$SESSION" AGENT_BROWSER_SESSION="$SESSION"
MSG="You were interrupted (an engine or account switch, a quota pause, or a restart). You are still $NAME. Continue where you stopped: keep updating $OUT/report.json and your memory, finish every not_run scenario and the sweep, then give the 2-line summary."
limit_hit() { tail -c 4000 "$1" 2>/dev/null | grep -qiE "hit your usage limit|usage limit reached|hit your limit|rate limit exceeded|out of extra usage|quota exceeded"; }
# markers older than RETRY_PRIMARY_MIN expire, so the primary engine gets another try after a limit
expire_marker() { [ -f "$1" ] && [ $(( $(date +%s) - $(mtime "$1") )) -ge $(( ${RETRY_PRIMARY_MIN:-60} * 60 )) ] && rm -f "$1"; }

# run_engine <engine> <model> <effort> <log>: one attempt, fresh or resumed
run_engine() {
  local E="$1" M="$2" F="$3" LOG="$4" SID
  case "$E" in
    codex)
      SID=$(grep -h -m1 '^session id:' "$OUT"/tester*.log 2>/dev/null | tail -1 | awk '{print $3}')
      if [ -n "$RESUME" ] && [ -n "$SID" ] && [ "$PREV_ENGINE" = codex ]; then
        timeout "$ROUND_TIMEOUT_S" codex exec resume "$SID" --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
          ${M:+-m "$M"} ${F:+-c model_reasoning_effort="$F"} "$MSG" > "$LOG" 2>&1
      else
        timeout "$ROUND_TIMEOUT_S" codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
          ${M:+-m "$M"} ${F:+-c model_reasoning_effort="$F"} < "$OUT/prompt.md" > "$LOG" 2>&1
      fi ;;
    claude)
      SID=$(cat "$OUT/claude-session" 2>/dev/null)
      if [ -n "$RESUME" ] && [ -n "$SID" ] && [ "$PREV_ENGINE" = claude ]; then
        timeout "$ROUND_TIMEOUT_S" claude -p --resume "$SID" ${M:+--model "$M"} ${F:+--effort "$F"} \
          --dangerously-skip-permissions "$MSG" < /dev/null > "$LOG" 2>&1
      else
        SID=$(uuidgen | tr 'A-Z' 'a-z'); echo "$SID" > "$OUT/claude-session"
        timeout "$ROUND_TIMEOUT_S" claude -p --session-id "$SID" ${M:+--model "$M"} ${F:+--effort "$F"} \
          --dangerously-skip-permissions < "$OUT/prompt.md" > "$LOG" 2>&1
      fi ;;
    opencode)
      if [ -n "$RESUME" ] && [ "$PREV_ENGINE" = opencode ] && [ -s "$OUT/opencode-session" ]; then
        timeout "$ROUND_TIMEOUT_S" opencode run --auto -s "$(cat "$OUT/opencode-session")" ${M:+-m "$M"} "$MSG" > "$LOG" 2>&1
      else
        timeout "$ROUND_TIMEOUT_S" opencode run --auto --title "crowd-$LANE-d$ROUND" ${M:+-m "$M"} -f "$OUT/prompt.md" \
          "Read the attached file: it is your full brief. Follow it exactly." > "$LOG" 2>&1
        opencode session list 2>/dev/null | grep -m1 "crowd-$LANE-d$ROUND" | awk '{print $1}' > "$OUT/opencode-session"
      fi ;;
    *) echo "unknown engine $E" > "$LOG"; return 2 ;;
  esac
}

TRIES=0
while :; do
  TRIES=$((TRIES+1)); [ "$TRIES" -gt 30 ] && done_with "exit=1 rc=too-many-switches"
  . "$RUN/config.env"   # re-read each attempt, so model and effort changes apply on the next switch
  expire_marker "$RUN/TESTER_FALLBACK"; expire_marker "$RUN/QUOTA_PAUSE"
  [ -f "$RUN/QUOTA_PAUSE" ] && done_with "exit=quota rc=all-engines-out"
  ENGINE="${TESTER:-codex}"; MODEL="${TESTER_MODEL:-}"; EFFORT="${TESTER_EFFORT:-}"
  if [ -f "$RUN/TESTER_FALLBACK" ] && [ -n "${FALLBACK_TESTER:-}" ]; then
    ENGINE="$FALLBACK_TESTER"; MODEL="${FALLBACK_MODEL:-}"; EFFORT="${FALLBACK_EFFORT:-}"
  fi
  PREV_ENGINE=$(cat "$OUT/engine" 2>/dev/null); echo "$ENGINE" > "$OUT/engine"
  RESUME=""; [ -s "$OUT/report.json" ] && { cp "$OUT/report.json" "$OUT/report.partial.json"; RESUME=1; }
  N=$(ls "$OUT"/tester*.log 2>/dev/null | wc -l | tr -d ' ')
  LOG="$OUT/tester.log"; [ "$N" -gt 0 ] && LOG="$OUT/tester.run$N.log"
  state "running" "{\"engine\": \"$ENGINE\", \"device\": \"$DEVICE\", \"surface\": \"$SURFACE\"}"
  echo "$(date) attempt $TRIES engine=$ENGINE prev=${PREV_ENGINE:-none} resume=${RESUME:-no}" >> "$OUT/resume.log"
  if [ -n "$RESUME" ] && [ "$PREV_ENGINE" != "$ENGINE" ]; then
    printf '\n## RESUME (%s)\nThis day was started by another tester and interrupted. Partial progress: %s/report.partial.json, evidence in %s/shots/ and %s/evidence/, your memory in memory.md. Copy the partial report to report.json, keep its valid results and evidence, continue with every not_run scenario.\n' "$(date +%H:%M)" "$OUT" "$OUT" "$OUT" >> "$OUT/prompt.md"
  fi
  run_engine "$ENGINE" "$MODEL" "$EFFORT" "$LOG"; RC=$?
  if limit_hit "$LOG"; then
    if [ ! -f "$RUN/TESTER_FALLBACK" ] && [ -n "${FALLBACK_TESTER:-}" ]; then
      # the primary ran out: every character's next attempt uses the fallback (this one right away)
      echo "since=$(date +%s) from=$ENGINE to=$FALLBACK_TESTER lane=$LANE" > "$RUN/TESTER_FALLBACK"
      continue
    fi
    # the fallback is out too, or there is none: the day stops; the workflow resumes it later
    [ -f "$RUN/QUOTA_PAUSE" ] || echo "which=all-engines reason=$ENGINE-limit lane=$LANE at $(date)" > "$RUN/QUOTA_PAUSE"
    done_with "exit=quota rc=$ENGINE-limit"
  fi
  break
done
case "$SURFACE" in
  web) agent-browser close >/dev/null 2>&1; AGENT_BROWSER_SESSION="$SESSION-2" agent-browser close >/dev/null 2>&1 ;;
  ios|android) agent-device close >/dev/null 2>&1 ;;
esac
done_with "exit=$RC engine=$ENGINE"
