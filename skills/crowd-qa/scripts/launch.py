#!/usr/bin/env python3
"""usage:
  launch.py <run-dir> record <run-id>     after Workflow returns, record its run id
  launch.py <run-dir> show                print what a resume needs: scriptPath, args, last run id

Before launching, write $RUN/launch.json yourself: {"scriptPath": "...", "args": {...}} with the exact
args object you pass to Workflow. Replay (resumeFromRunId) only returns cached agent results when the
script and args are unchanged, so a resume must pass this same object back."""
import json, sys, time

run, cmd = sys.argv[1], sys.argv[2]
path = f"{run}/launch.json"
data = json.load(open(path))
if cmd == 'record':
    data.setdefault('runs', []).append({'run_id': sys.argv[3], 'at': time.strftime('%Y-%m-%d %H:%M:%S')})
    json.dump(data, open(path, 'w'), indent=1)
    print(f"recorded {sys.argv[3]}")
elif cmd == 'show':
    runs = data.get('runs', [])
    print(json.dumps({'scriptPath': data['scriptPath'], 'resumeFromRunId': runs[-1]['run_id'] if runs else None,
                      'args': data['args']}, indent=1))
else:
    sys.exit(__doc__)
