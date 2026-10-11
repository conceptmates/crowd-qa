#!/usr/bin/env bash
# usage: extra-browser.sh <run> <lane> [n]   -> prints the agent-browser session to use for a second (third...) user
# For a tester who needs another person signed in at the same time. With BROWSER_SLOTS set, it takes a slot from
# the same pool as the characters (remote first), so REMOTE_HOST never runs more browsers than there are slots;
# keep one slot spare for this. The slot is held by the character's runner and freed when its day ends.
# A remote extra browser prints the AGENT_BROWSER_CDP value to pass with every call: the tester's own shell has its
# own port in AGENT_BROWSER_CDP, so the session name alone would reach the wrong browser.
set -u
RUN="$1"; LANE="$2"; N="${3:-2}"
. "$RUN/config.env"
NAME="$LANE-$N"; SESSION="crowd-$NAME"
if [ -z "${BROWSER_SLOTS:-}" ]; then
  echo "$SESSION"; echo "Use it with: AGENT_BROWSER_SESSION=$SESSION agent-browser open <url>" >&2; exit 0
fi
# the slot is held by the character's runner (RUNNER_PID, set for every tester), so it ends with the day
OWNER="${RUNNER_PID:-$(pgrep -f "run-tester.sh $RUN $LANE " | head -1)}"; [ -z "$OWNER" ] && OWNER=$$
SLOT=$(bash "$RUN/scripts/browser-pool.sh" "$RUN" acquire "$LANE" "$OWNER" | tail -1)
echo "$SLOT" > "$RUN/.browsers/extra-$NAME"
case "$SLOT" in
  remote:*)
    PORT=$(bash "$RUN/scripts/remote-browser.sh" "$RUN" start "$NAME" "$SLOT" 2>>"$RUN/logs/extra-browser.log") \
      || { echo "could not start a remote browser (see logs/extra-browser.log)" >&2; exit 1; }
    echo "$SESSION"
    echo "Use it with: AGENT_BROWSER_SESSION=$SESSION AGENT_BROWSER_CDP=$PORT agent-browser open <url>   (slot $SLOT on $REMOTE_HOST)" >&2 ;;
  *)
    echo "$SESSION"
    echo "Use it with: env -u AGENT_BROWSER_CDP -u AGENT_BROWSER_EXECUTABLE_PATH AGENT_BROWSER_SESSION=$SESSION agent-browser open <url>   (local slot $SLOT)" >&2 ;;
esac
