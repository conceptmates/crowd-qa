#!/usr/bin/env bash
# A browser of its own for one fix, review or re-check job, from the same slot pool as the crowd (BROWSER_SLOTS).
#   eval "$(fix-browser.sh <run> acquire <job>)"   prints export lines: AGENT_BROWSER_SESSION (+ CDP for remote)
#   fix-browser.sh <run> release <job>
# Agents run each command in a fresh shell, so the slot is held by a small holder process started here, not by the
# caller's shell; release stops it. Calling acquire again for the same job prints the same browser.
# On 2026-10-13 one re-check agent found other agents' pages in its Chromium: two jobs had attached to one remote
# browser. Here every job gets its own named session, and a remote slot is only granted through browser-pool.sh,
# which refuses a CDP port someone else already holds.
set -u
RUN="$1"; CMD="$2"; JOB="$3"
. "$RUN/config.env"
D="$RUN/.browsers"; mkdir -p "$D"
NAME="job-$JOB"; SESSION="crowd-$NAME"
case "$CMD" in
  acquire)
    if [ -f "$D/$NAME.env" ] && kill -0 "$(cat "$D/$NAME.holder" 2>/dev/null)" 2>/dev/null; then cat "$D/$NAME.env"; exit 0; fi
    nohup sleep 86400 >/dev/null 2>&1 < /dev/null &
    echo $! > "$D/$NAME.holder"
    if [ -z "${BROWSER_SLOTS:-}" ]; then
      echo "export AGENT_BROWSER_SESSION=$SESSION" | tee "$D/$NAME.env"; exit 0
    fi
    SLOT=$(bash "$RUN/scripts/browser-pool.sh" "$RUN" acquire "$NAME" "$(cat "$D/$NAME.holder")" | tail -1)
    case "$SLOT" in
      remote:*)
        CDP=$(bash "$RUN/scripts/remote-browser.sh" "$RUN" start "$NAME" "$SLOT") || { echo "remote browser did not start" >&2; exit 1; }
        printf 'export AGENT_BROWSER_SESSION=%s AGENT_BROWSER_CDP=%s AGENT_BROWSER_EXECUTABLE_PATH=%s\n' \
          "$SESSION" "$CDP" "$RUN/scripts/no-local-browser.sh" | tee "$D/$NAME.env" ;;
      *) echo "export AGENT_BROWSER_SESSION=$SESSION" | tee "$D/$NAME.env" ;;
    esac ;;
  release)
    if [ -f "$D/$NAME.env" ]; then (eval "$(cat "$D/$NAME.env")"; agent-browser close >/dev/null 2>&1); fi
    bash "$RUN/scripts/remote-browser.sh" "$RUN" stop "$NAME" >/dev/null 2>&1
    bash "$RUN/scripts/browser-pool.sh" "$RUN" release "$NAME" >/dev/null 2>&1
    kill "$(cat "$D/$NAME.holder" 2>/dev/null)" 2>/dev/null
    rm -f "$D/$NAME.env" "$D/$NAME.holder"; echo "released $SESSION" ;;
  *) sed -n 2,4p "$0"; exit 2 ;;
esac
