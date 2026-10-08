#!/usr/bin/env python3
"""judge-pack.py <run> <character> <day>: one file with everything a judge needs, so the judge reads one file
instead of hunting through reports, logs and the square (that hunting was half of every judge's tokens).

Writes lanes/<id>/round<day>/judge-pack.md:
  - the runner's DONE line and the scenarios with their status and notes
  - each finding with its evidence paths (screenshots downscaled to shots-small/, 900 px, when sips or PIL exists)
  - the square posts and replies by this character, and replies to them
  - the last 60 lines of the newest tester log
Prints the pack's path.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

run, cid, day = sys.argv[1], sys.argv[2], int(sys.argv[3])
rd = os.path.join(run, "lanes", cid, f"round{day}")
small = os.path.join(rd, "shots-small")


def shrink(path):
    """A 900 px copy of a screenshot; the original path when no tool is available."""
    if not path.lower().endswith(".png") or not os.path.exists(path):
        return path
    os.makedirs(small, exist_ok=True)
    out = os.path.join(small, os.path.basename(path))
    if os.path.exists(out):
        return out
    if shutil.which("sips"):
        r = subprocess.run(["sips", "-Z", "900", path, "--out", out], capture_output=True)
        if r.returncode == 0:
            return out
    try:
        from PIL import Image  # noqa: E402
        im = Image.open(path)
        im.thumbnail((900, 900))
        im.save(out)
        return out
    except Exception:
        return path


def absolute(p):
    return p if os.path.isabs(p) else os.path.join(rd, p)


lines = [f"# Judge pack: {cid}, day {day}", ""]
done = os.path.join(rd, "DONE")
lines.append(f"Runner: {open(done).read().strip() if os.path.exists(done) else '(no DONE)'}")
rep = {}
try:
    rep = json.load(open(os.path.join(rd, "report.json")))
except Exception as e:
    lines.append(f"report.json unreadable: {e}")

sc = rep.get("scenarios", [])
lines += ["", f"## Scenarios ({len(sc)})", "| id | status | notes | evidence |", "|---|---|---|---|"]
for s in sc:
    ev = ", ".join(shrink(absolute(p)) for p in s.get("evidence", [])[:3])
    note = (s.get("notes") or "").replace("|", "/").replace("\n", " ")[:220]
    lines.append(f"| {s.get('id')} | {s.get('status')} | {note} | {ev} |")

fs = rep.get("findings", [])
lines += ["", f"## Findings ({len(fs)})"]
for f in fs:
    lines += ["", f"### {f.get('id')}: {f.get('title')}",
              f"- type/severity: {f.get('type')} / {f.get('severity')}; route: {f.get('route')}; viewport: {f.get('viewport')}",
              f"- voice: {f.get('voice')}",
              f"- steps: {' → '.join(f.get('steps', []))}",
              f"- expected: {f.get('expected')}",
              f"- actual: {f.get('actual')}",
              f"- mechanism (tester, confidence {f.get('mechanism_confidence')}): {f.get('mechanism') or '(none)'}",
              f"- evidence: {', '.join(shrink(absolute(p)) for p in f.get('screenshots', []))}"]
    if f.get("console_errors"):
        lines.append(f"- console: {'; '.join(f['console_errors'])[:400]}")
    if f.get("square_post"):
        lines.append(f"- square post: {f['square_post']}")

feed = []
fp = os.path.join(run, "square", "feed.jsonl")
if os.path.exists(fp):
    feed = [json.loads(l) for l in open(fp) if l.strip()]
mine = {e["id"] for e in feed if e.get("author") == cid}
rel = [e for e in feed if e.get("author") == cid or e.get("ref") in mine]
lines += ["", f"## Square ({len(rel)} posts by or replying to {cid})"]
for e in rel[-40:]:
    ref = f" → {e['ref']}" if e.get("ref") else ""
    shot = f" [{shrink(e['shot'])}]" if e.get("shot") else ""
    lines.append(f"- {e['id']}{ref} {e.get('kind')} by {e.get('author')}: {e.get('text', '')[:300]}{shot}")

logs = sorted(glob.glob(os.path.join(rd, "tester*.log")), key=os.path.getmtime)
if logs:
    tail = open(logs[-1], errors="replace").read().splitlines()[-60:]
    lines += ["", f"## Last 60 lines of {os.path.basename(logs[-1])}", "```", *[t[:300] for t in tail], "```"]

out = os.path.join(rd, "judge-pack.md")
open(out, "w").write("\n".join(lines) + "\n")
print(out)
