#!/usr/bin/env bash
# FILE_CMD for the dry run: "files" an issue as a markdown file instead of a tracker, prints its path.
# usage: file-local.sh <issue.json>   (writes next to the run dir's state/, in filed-issues/)
J="$1"; OUT="$(cd "$(dirname "$J")/.." && pwd)/filed-issues"; mkdir -p "$OUT"
N=$(( $(ls "$OUT" | wc -l) + 1 )); F="$OUT/issue-$N.md"
python3 - "$J" "$F" <<'PY'
import json, sys
j = json.load(open(sys.argv[1]))
open(sys.argv[2], "w").write(f"# {j['title']}\n\nlabels: {', '.join(j.get('labels', []))}\n\n{j['body']}\n")
PY
echo "file://$F"
