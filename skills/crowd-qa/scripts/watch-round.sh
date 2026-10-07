#!/usr/bin/env bash
# usage: watch-round.sh <run-dir> <lane> <round> [max-seconds=570]
# Waits for a round, bounded so one call fits inside a tool timeout. Call it repeatedly.
# Exit 0: DONE exists (prints it). Exit 2: still running or waiting for capacity (prints progress).
# Exit 3: runner is gone without DONE — writes DONE so nothing waits forever.
RUN="$1"; LANE="$2"; ROUND="$3"; MAX="${4:-570}"
OUT="$RUN/lanes/$LANE/round$ROUND"
end=$(( $(date +%s) + MAX ))
while [ "$(date +%s)" -lt "$end" ]; do
  [ -f "$OUT/DONE" ] && { cat "$OUT/DONE"; exit 0; }
  if ! pgrep -f "run-tester.sh $RUN $LANE $ROUND\$" >/dev/null; then
    sleep 5; [ -f "$OUT/DONE" ] && { cat "$OUT/DONE"; exit 0; }
    if [ -f "$RUN/QUOTA_PAUSE" ] || cat "$OUT"/tester*.log 2>/dev/null | tail -c 4000 | grep -qE "^(ERROR: ?)?You.ve hit your usage limit"; then
      echo "exit=quota rc=runner-died" > "$OUT/DONE"; else echo "exit=1 rc=runner-died" > "$OUT/DONE"; fi
    cat "$OUT/DONE"; exit 3
  fi
  sleep 20
done
st=$(python3 -c "import json;d=json.load(open('$RUN/lanes/$LANE/state.json'));print(d.get('status'))" 2>/dev/null)
shots=$(ls "$OUT/shots" 2>/dev/null | wc -l | tr -d ' ')
echo "still $st: shots=$shots"
exit 2
