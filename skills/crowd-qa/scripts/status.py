#!/usr/bin/env python3
"""usage: status.py <run-dir>
One-screen status of a crowd-qa run: per-lane round/state/scenarios/findings/screenshots,
plus fallback/outage markers, capacity and memory tails. Read-only."""
import glob, json, os, subprocess, sys, time

run = sys.argv[1]
def tail(path, n=1):
    try:
        with open(path) as f: return [l.rstrip() for l in f.readlines()[-n:]]
    except OSError: return []

running = subprocess.run(["pgrep", "-fl", f"run-tester.sh {run} "], capture_output=True, text=True).stdout
print(f"{time.strftime('%H:%M:%S')}  run: {run}")
print(f"{'lane':24} {'round':6} {'state':18} {'scenarios':34} {'find':5} {'shots':5} verdict")
for lane_dir in sorted(glob.glob(f"{run}/lanes/*/")):
    lane = os.path.basename(lane_dir.rstrip("/"))
    rounds = sorted(glob.glob(f"{lane_dir}round*"), key=lambda p: int(p.rsplit("round", 1)[-1]))
    st = {}
    try: st = json.load(open(f"{lane_dir}state.json"))
    except Exception: pass
    if not rounds:
        print(f"{lane:24} {'-':6} {st.get('status', 'queued'):18}"); continue
    r = rounds[-1]; rn = r.rsplit("round", 1)[-1]
    state = st.get("status", "?")
    if os.path.exists(f"{r}/DONE"): state = open(f"{r}/DONE").read().strip()[:18]
    elif f" {lane} {rn}" in running: state = st.get("status", "running")
    scen, find = "-", "-"
    try:
        rep = json.load(open(f"{r}/report.json")); c = {}
        for s in rep.get("scenarios", []): c[s.get("status")] = c.get(s.get("status"), 0) + 1
        scen = f"{len(rep.get('scenarios', []))} " + ",".join(f"{k}:{v}" for k, v in c.items())
        find = len(rep.get("findings", []))
    except Exception: pass
    verdict = "-"
    try:
        v = json.load(open(f"{r}/verdict.json"))
        verdict = f"cov {v.get('coverage_score')} sat={v.get('satisfied')} verified={len(v.get('verified_findings', []))}"
    except Exception: pass
    print(f"{lane:24} {rn:6} {state:18} {scen[:34]:34} {str(find):5} {len(glob.glob(r + '/shots/*.png')):<5} {verdict}")
print()
if os.path.exists(f"{run}/TESTER_FALLBACK"): print("FALLBACK: ", open(f"{run}/TESTER_FALLBACK").read().strip())
if os.path.exists(f"{run}/STACK_DOWN"): print("STACK DOWN:", open(f"{run}/STACK_DOWN").read().strip())
if os.path.exists(f"{run}/QUOTA_PAUSE"): print("PAUSED:   ", open(f"{run}/QUOTA_PAUSE").read().strip())
print("capacity: ", *(tail(f"{run}/capacity.log") or ["-"]))
print("memory:   ", *(tail(f"{run}/memlog.txt") or ["(memlog not running?)"]))
