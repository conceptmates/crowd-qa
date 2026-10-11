#!/usr/bin/env bash
# usage: quota-guard.sh <run-dir> [once]   (restart-watchers.sh starts it when CODEX_ACCOUNTS or CLAUDE_ACCOUNTS is set)
# Rotates subscription accounts through caam (Coding Agent Account Manager)
# before testers hit a limit. Every GUARD_POLL_S (120 s) it reads `caam limits <tool>` for the accounts in
# CODEX_ACCOUNTS and CLAUDE_ACCOUNTS (caam profile names, in order of preference):
#   1. the active account stays while it is under PRIMARY_MAX % of its 5-hour window and WEEKLY_MAX % of its week;
#   2. otherwise the first account with SWITCH_HEADROOM points to spare is activated (`caam activate` swaps only the
#      auth file), and this run's testers on that engine are stopped. The runner sees .engine-switched and resumes
#      each one in the SAME session with a two-line message, never the whole brief again;
#   3. Claude, no account with a reading left: an account caam cannot read (often every one but the active) may still
#      be fresh, so the guard activates it and makes one tiny test call (try_unread); if that fails it switches back;
#   4. no account left: Codex with FALLBACK_TESTER set falls back instead of pausing (below); otherwise
#      QUOTA_PAUSE.<engine> holds that engine's characters and is lifted as soon as an account has room.
# "No reading" for the active account (caam returns no numbers now and then) is never a limit: nothing is decided
# that round. It caused 6 false pauses in one run before this rule.
# caam activate is machine-wide: a Claude switch also moves the orchestrating session and crowd.py's Opus steps.
# So only this guard switches accounts during a run. A switch by hand while planners or judges run fails them
# (29 failed calls and 6 replans in one run).
set -u
RUN="$1"; MODE="${2:-loop}"
CAAM="${CAAM:-caam}"
mkdir -p "$RUN/logs"
[ "$MODE" = loop ] && echo $$ > "$RUN/logs/quota-guard.pid"

log() { echo "$(date +%H:%M:%S) $*" >> "$RUN/quota.log"; }
kids() { local c; for c in $(pgrep -P "$1"); do kids "$c"; echo "$c"; done; }
stop_testers() {  # stop_testers <codex|claude>: SIGTERM that engine's processes under this run's runners
  local r k
  for r in $(pgrep -f "run-tester.sh $RUN "); do
    for k in $(kids "$r"); do ps -o command= -p "$k" | grep -q "$1" && kill -TERM "$k" 2>/dev/null; done
  done
}
switched() { touch "$RUN/.engine-switched"; stop_testers "$1"; }
active() { $CAAM ls "$1" 2>/dev/null | awk '$1=="●"{print $2}'; }
limits() {  # limits <tool> -> "<profile> <primary%> <weekly%> <status>" per line; "-" = unknown
  timeout 90 $CAAM limits "$1" 2>/dev/null | awk -v t="$1" '$1 ~ "^" t "/" {
    p=$3; s=$4; gsub("%","",p); gsub("%","",s); st=$NF; sub(t "/","",$1); print $1, p, s, st }'
}
fits() {  # fits <primary> <weekly> <margin>: under both bars by at least margin points
  { [ -z "$1" ] || [ "$1" = "-" ]; } && return 1
  python3 -c "import sys;p=float('$1');w=0 if '$2' in ('','-') else float('$2');sys.exit(0 if p<$PRIMARY_MAX-$3 and w<$WEEKLY_MAX-$3 else 1)"
}
row() { echo "$L" | awk -v a="$1" '$1==a'; }
first_fit() {  # first_fit <margin> [skip]: the first account in the configured order with room
  local a _ AP AW AST
  for a in $ACCOUNTS; do
    [ "$a" = "${2:-}" ] && continue
    read -r _ AP AW AST < <(row "$a")
    [ "${AST:-}" = ok ] && fits "${AP:-}" "${AW:-}" "$1" && { echo "$a"; return 0; }
  done; return 1
}
try_unread() {  # try_unread <current>: activate an unread Claude account and keep it if a test call works
  local cur="$1" a r _ AP AW AST
  # an Opus step (crowd.py: claude -p ... --output-format json) would fail mid-call; wait unless it fails anyway
  if pgrep -f "claude -p .*--output-format json" >/dev/null && fits "$P" "$W" 0; then
    log "claude: an Opus step is running; trying unread accounts next round"; return 1
  fi
  for a in $ACCOUNTS; do
    [ "$a" = "$cur" ] && continue
    read -r _ AP AW AST < <(row "$a"); [ "${AST:-}" = ok ] && continue
    $CAAM activate claude "$a" >/dev/null 2>&1 || continue
    r=$(cd /tmp && timeout 90 claude -p --model "${CLAUDE_PROBE_MODEL:-haiku}" --setting-sources project --strict-mcp-config \
          "reply with the single word ok" < /dev/null 2>&1 | tail -1)
    if [ "$r" = ok ]; then echo "$a"; return 0; fi
    log "claude: $a refused a test call (${r:0:80}); back to $cur"; $CAAM activate claude "$cur" >/dev/null 2>&1
  done; return 1
}
read_active() {  # sets L, CUR, P, W; returns 1 when there is nothing to decide on this round
  L=$(limits "$T"); CUR=$(active "$T")
  [ -z "$L" ] && { log "$T: caam limits gave nothing; keeping $CUR"; return 1; }
  read -r _ P W ST < <(row "$CUR")
  if [ -z "${P:-}" ] || [ "$P" = "-" ]; then log "$T: no reading for active $CUR; keeping it"; return 1; fi
  return 0
}

guard() {  # guard <tool> <accounts>: rotate, then pause when every account is near its limit
  T="$1"; ACCOUNTS="$2"; [ -z "$ACCOUNTS" ] && return
  read_active || return
  local PAUSED=""; [ -f "$RUN/QUOTA_PAUSE.$T" ] && PAUSED=1
  log "$T active=$CUR primary=$P% weekly=${W:--}% paused=${PAUSED:-no} | $(echo "$L" | awk '{printf "%s:%s/%s ",$1,$2,$3}')"
  if [ -z "$PAUSED" ]; then
    fits "$P" "$W" 0 && return
    if NEXT=$(first_fit "$HEADROOM" "$CUR"); then
      if $CAAM activate "$T" "$NEXT" >/dev/null 2>&1; then
        log "SWITCH $T $CUR -> $NEXT ($CUR at $P%/${W:--}%); testers resume their same session"; switched "$T"
      else log "SWITCH $T to $NEXT failed (caam activate)"; fi
    elif [ "$T" = claude ] && NEXT=$(try_unread "$CUR"); then
      log "SWITCH claude $CUR -> $NEXT (no reading for $NEXT, but a test call worked); testers resume their same session"; switched claude
    else
      echo "engine=$T reason=all-accounts-near-limit at $(date)" > "$RUN/QUOTA_PAUSE.$T"
      log "PAUSE $T: every account near its limit; its characters hold, the other engine continues"; switched "$T"
    fi
  elif NEXT=$(first_fit "$HEADROOM"); then
    [ "$NEXT" != "$CUR" ] && $CAAM activate "$T" "$NEXT" >/dev/null 2>&1
    mv "$RUN/QUOTA_PAUSE.$T" "$RUN/logs/QUOTA_PAUSE.$T.lifted-$(date +%m%d-%H%M%S)"
    log "RESUME $T on $NEXT (window reset)"
  fi
}

# Codex with FALLBACK_TESTER: rotate accounts; with none free, fall back instead of pausing.
#   active >= CODEX_NEAR_MAX and no other account free -> TESTER_FALLBACK (by=guard): new character-days start on
#     the fallback; running Codex testers carry on until Codex really refuses, then the runner hands each over
#     from its report.json;
#   an account free again (with SWITCH_HEADROOM) -> activate it and remove the marker: new days use Codex again.
guard_codex() {
  T=codex; ACCOUNTS="${CODEX_ACCOUNTS:-}"; [ -z "$ACCOUNTS" ] && return
  [ -z "${FALLBACK_TESTER:-}" ] || [ "$FALLBACK_TESTER" = codex ] && { guard codex "$ACCOUNTS"; return; }
  local NEAR="${CODEX_NEAR_MAX:-85}" FB=""
  read_active || return
  [ -f "$RUN/TESTER_FALLBACK" ] && FB=1
  log "codex active=$CUR primary=$P% weekly=${W:--}% fallback=${FB:-no} | $(echo "$L" | awk '{printf "%s:%s/%s ",$1,$2,$3}')"
  if fits "$P" "$W" $((PRIMARY_MAX - NEAR)); then
    if [ -n "$FB" ] && grep -q "by=guard" "$RUN/TESTER_FALLBACK"; then
      rm -f "$RUN/TESTER_FALLBACK"; log "codex: $CUR has room again; new days start on Codex"
    fi
    return
  fi
  if NEXT=$(first_fit "$HEADROOM" "$CUR"); then
    if $CAAM activate codex "$NEXT" >/dev/null 2>&1; then
      grep -q "by=guard" "$RUN/TESTER_FALLBACK" 2>/dev/null && rm -f "$RUN/TESTER_FALLBACK"
      log "SWITCH codex $CUR -> $NEXT ($CUR at $P%/${W:--}%); Codex testers resume their same session"; switched codex
    else log "SWITCH codex to $NEXT failed (caam activate)"; fi
  elif [ -z "$FB" ]; then
    echo "since=$(date +%s) from=codex to=$FALLBACK_TESTER by=guard near=$CUR:$P%" > "$RUN/TESTER_FALLBACK"
    log "FALLBACK codex near its limit on every account ($CUR at $P%): new days start on $FALLBACK_TESTER; running Codex testers continue until Codex refuses"
  fi
}

while :; do
  . "$RUN/config.env"
  HEADROOM="${SWITCH_HEADROOM:-10}"; PRIMARY_MAX="${PRIMARY_MAX:-94}"; WEEKLY_MAX="${WEEKLY_MAX:-95}"
  guard_codex
  guard claude "${CLAUDE_ACCOUNTS:-}"
  [ "$MODE" = once ] && exit 0
  sleep "${GUARD_POLL_S:-120}"
done
