#!/usr/bin/env bash
# Saves state/ every 5 minutes for as long as the run dir exists. Stop: kill $(cat <run>/logs/state-saver.pid)
RUN="$1"; echo $$ > "$RUN/logs/state-saver.pid"
while [ -d "$RUN" ]; do python3 "$RUN/scripts/save-state.py" "$RUN" >> "$RUN/logs/state-saver.log" 2>&1; sleep 300; done
