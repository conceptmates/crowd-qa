#!/usr/bin/env bash
# Set as AGENT_BROWSER_EXECUTABLE_PATH whenever the browsers are remote (BROWSER_SLOTS remote:N). agent-browser
# talks to the remote Chromium through AGENT_BROWSER_CDP; if it ever tries to launch a browser on this machine
# instead (the link dropped, or a session was opened without the CDP port), this refuses, loudly, and logs it.
# `agent-browser connect` used to start an idle local Chrome per session (39-66 Chrome processes on the
# orchestrating Mac in one run) and fell back to it when the remote link dropped, so evidence came from the
# wrong browser.
LOG="${RUN:+$RUN/logs/no-local-browser.log}"; LOG="${LOG:-$HOME/.crowd-qa-no-local-browser.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null
echo "local browser refused on $(hostname): every browser in this run is remote. Use your session with AGENT_BROWSER_CDP set (the runner sets it; extra-browser.sh prints it)." >&2
echo "$(date '+%F %T') refused a local browser launch${LANE:+ (lane $LANE)}: $*" >> "$LOG"
exit 1
